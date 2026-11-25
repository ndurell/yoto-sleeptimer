"""Watch live playback on every Yoto player on the account and arm a sleep
timer whenever one starts playing without a timer already running."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Dict

import aiohttp

from . import token_store
from .config import Config
from .logic import player_selected, should_arm

log = logging.getLogger("yoto-sleeptimer")

# A player that is playing but hasn't reported whether a sleep timer is
# running gets this long to report it (the live connection asks every player
# for a full events report when it connects). If it still hasn't, assume no
# timer is running, or a player that never reports the field would never be
# armed at all.
UNKNOWN_TIMER_GRACE_SECONDS = 20


async def run_once(cfg: Config, session: aiohttp.ClientSession) -> None:
    from yoto_api import YotoClient
    from yoto_api.models import PlaybackStatus

    refresh_token = token_store.read(cfg.token_file)

    async def persist_token(token) -> None:
        # The library rotates the refresh token on its own; save every new one
        # or the next restart would use a token that no longer works.
        token_store.write(cfg.token_file, token.refresh_token)
        log.info("Refresh token saved.")

    last_arm: Dict[str, float] = {}
    pending_unknown: Dict[str, asyncio.Task] = {}
    sleep_seconds = cfg.sleep_minutes * 60

    async with YotoClient(client_id=cfg.client_id, session=session,
                          refresh_hook=persist_token) as client:
        client.set_refresh_token(refresh_token)
        await client.refresh()
        await client.update_player_list()

        players = {pid: p for pid, p in client.players.items()
                   if player_selected(cfg.players, pid, p.device.name)}
        if not client.players:
            raise RuntimeError("no Yoto players found on this account")
        if not players:
            names = [p.device.name for p in client.players.values()]
            raise RuntimeError(
                f"PLAYERS matched none of this account's players: {names}")
        log.info("Watching %d player(s): %s", len(players),
                 ", ".join(f"{p.device.name} ({pid})" for pid, p in players.items()))

        async def recheck_unknown(player) -> None:
            await asyncio.sleep(UNKNOWN_TIMER_GRACE_SECONDS)
            pending_unknown.pop(player.device.device_id, None)
            ev = player.last_event
            if ev is None or ev.sleep_timer_active is not None:
                return  # it reported in the meantime; that update decided
            log.info("%s never reported its sleep timer state; assuming none is set.",
                     player.device.name)
            await evaluate(player, assume_no_timer=True)

        async def on_update(player) -> None:
            await evaluate(player)

        async def evaluate(player, assume_no_timer: bool = False) -> None:
            ev = player.last_event
            if ev is None:
                return
            pid = player.device.device_id
            if pid not in players:
                return
            playing = ev.playback_status == PlaybackStatus.PLAYING
            timer_active = ev.sleep_timer_active
            if timer_active is None and assume_no_timer:
                timer_active = False
            if timer_active is None and playing and pid not in pending_unknown:
                log.debug("%s is playing but hasn't reported its timer state yet; waiting.",
                          player.device.name)
                pending_unknown[pid] = asyncio.ensure_future(recheck_unknown(player))
            now = time.monotonic()
            if not should_arm(
                playing=playing,
                sleep_timer_active=timer_active,
                now_monotonic=now,
                last_arm_monotonic=last_arm.get(pid),
                cooldown_seconds=cfg.cooldown_seconds,
                local_time=dt.datetime.now().time(),
                active_hours=cfg.active_hours,
            ):
                return
            last_arm[pid] = now
            if cfg.dry_run:
                log.info("[dry run] would arm a %d-min sleep timer on %s",
                         cfg.sleep_minutes, player.device.name)
                return
            try:
                await client.set_sleep_timer(pid, sleep_seconds)
                log.info("Armed %d-min sleep timer on %s", cfg.sleep_minutes, player.device.name)
            except Exception:
                last_arm.pop(pid, None)  # let the next event try again
                log.exception("Failed to arm sleep timer on %s", player.device.name)

        disconnected = asyncio.Event()

        async def on_disconnect(exc) -> None:
            log.warning("Live connection dropped (%s)", exc)
            disconnected.set()

        # connect_events() has no timeout of its own, so a hung handshake
        # would stall forever. Bound it and let the outer loop retry.
        try:
            await asyncio.wait_for(
                client.connect_events(list(players), on_update=on_update,
                                      on_disconnect=on_disconnect),
                timeout=cfg.connect_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"live connection timed out after {cfg.connect_timeout_seconds}s") from None
        log.info("Connected; waiting for playback.")
        try:
            await disconnected.wait()
        finally:
            for task in pending_unknown.values():
                task.cancel()
        raise RuntimeError("live connection lost")


async def run_forever(cfg: Config) -> None:
    if cfg.dry_run:
        log.info("DRY_RUN is on: timers will be logged, not set.")
    if cfg.active_hours:
        log.info("Only arming between %s and %s.",
                 cfg.active_hours.start.strftime("%H:%M"),
                 cfg.active_hours.end.strftime("%H:%M"))
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await run_once(cfg, session)
            except (FileNotFoundError, ValueError) as exc:
                log.error("%s", exc)  # token missing/empty: retrying won't help soon
            except Exception as exc:
                log.warning("Watcher stopped: %s", exc)
            log.info("Retrying in %ds...", cfg.retry_seconds)
            await asyncio.sleep(cfg.retry_seconds)
