"""Drive watcher.run_once with a fake YotoClient (no network)."""
import asyncio
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS

from yoto_sleeptimer import config, token_store, watcher


class PlaybackStatus(str, Enum):
    PLAYING = "playing"
    PAUSED = "paused"


async def default_scenario(client, on_update):
    p1 = client.players["p1"]
    p1.last_event = NS(playback_status=PlaybackStatus.PLAYING, sleep_timer_active=False)
    await on_update(p1)
    await on_update(p1)  # burst: cooldown must swallow it
    p1.last_event = NS(playback_status=PlaybackStatus.PLAYING, sleep_timer_active=True)
    await on_update(p1)
    p2 = client.players["p2"]
    p2.last_event = NS(playback_status=PlaybackStatus.PLAYING, sleep_timer_active=False)
    await on_update(p2)  # filtered out by PLAYERS


class FakeClient:
    instance = None
    scenario = staticmethod(default_scenario)

    def __init__(self, client_id, session, refresh_hook):
        self.refresh_hook = refresh_hook
        self.armed = []
        self.players = {
            "p1": NS(device=NS(device_id="p1", name="Bedroom"), last_event=None),
            "p2": NS(device=NS(device_id="p2", name="Playroom"), last_event=None),
        }
        FakeClient.instance = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    def set_refresh_token(self, token):
        self.token = token

    async def refresh(self):
        await self.refresh_hook(NS(refresh_token="rotated"))

    async def update_player_list(self):
        pass

    async def set_sleep_timer(self, pid, seconds):
        self.armed.append((pid, seconds))

    async def connect_events(self, ids, on_update, on_disconnect):
        self.subscribed = ids
        self.on_disconnect = on_disconnect
        await type(self).scenario(self, on_update)
        await on_disconnect(OSError("bye"))


class RunOnce(unittest.TestCase):
    def setUp(self):
        api = types.ModuleType("yoto_api")
        api.YotoClient = FakeClient
        models = types.ModuleType("yoto_api.models")
        models.PlaybackStatus = PlaybackStatus
        self._saved = {k: sys.modules.get(k) for k in ("yoto_api", "yoto_api.models")}
        sys.modules.update({"yoto_api": api, "yoto_api.models": models})
        self._grace = watcher.UNKNOWN_TIMER_GRACE_SECONDS
        watcher.UNKNOWN_TIMER_GRACE_SECONDS = 0.05

    def tearDown(self):
        watcher.UNKNOWN_TIMER_GRACE_SECONDS = self._grace
        FakeClient.scenario = staticmethod(default_scenario)
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_arms_once_and_rotates_token(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = replace(_cfg(), data_dir=Path(d), sleep_minutes=45,
                          players=frozenset({"bedroom"}))
            token_store.write(cfg.token_file, "original")
            with self.assertRaisesRegex(RuntimeError, "connection lost"):
                asyncio.run(watcher.run_once(cfg, session=None))
            client = FakeClient.instance
            self.assertEqual(client.subscribed, ["p1"])
            self.assertEqual(client.armed, [("p1", 45 * 60)])
            self.assertEqual(token_store.read(cfg.token_file), "rotated")

    def _run(self, scenario):
        FakeClient.scenario = staticmethod(scenario)
        with tempfile.TemporaryDirectory() as d:
            cfg = replace(_cfg(), data_dir=Path(d))
            token_store.write(cfg.token_file, "original")
            with self.assertRaisesRegex(RuntimeError, "connection lost"):
                asyncio.run(watcher.run_once(cfg, session=None))
        return FakeClient.instance.armed

    def test_unknown_timer_state_waits_for_report(self):
        async def scenario(client, on_update):
            p1 = client.players["p1"]
            p1.last_event = NS(playback_status=PlaybackStatus.PLAYING, sleep_timer_active=None)
            await on_update(p1)  # unknown: must not arm yet
            self.assertEqual(client.armed, [])
            p1.last_event.sleep_timer_active = True  # it had a manual timer
            await on_update(p1)
            await asyncio.sleep(0.1)  # past the grace period
        self.assertEqual(self._run(scenario), [])

    def test_unknown_timer_state_then_none_running(self):
        async def scenario(client, on_update):
            p1 = client.players["p1"]
            p1.last_event = NS(playback_status=PlaybackStatus.PLAYING, sleep_timer_active=None)
            await on_update(p1)
            p1.last_event.sleep_timer_active = False
            await on_update(p1)
            await asyncio.sleep(0.1)
        self.assertEqual(self._run(scenario), [("p1", 90 * 60)])

    def test_timer_state_never_reported_arms_after_grace(self):
        async def scenario(client, on_update):
            p1 = client.players["p1"]
            p1.last_event = NS(playback_status=PlaybackStatus.PLAYING, sleep_timer_active=None)
            await on_update(p1)
            await on_update(p1)  # a second unknown update must not double-schedule
            self.assertEqual(client.armed, [])
            await asyncio.sleep(0.1)
        self.assertEqual(self._run(scenario), [("p1", 90 * 60)])

    def test_unknown_then_paused_does_not_arm(self):
        async def scenario(client, on_update):
            p1 = client.players["p1"]
            p1.last_event = NS(playback_status=PlaybackStatus.PLAYING, sleep_timer_active=None)
            await on_update(p1)
            p1.last_event.playback_status = PlaybackStatus.PAUSED
            await on_update(p1)
            await asyncio.sleep(0.1)
        self.assertEqual(self._run(scenario), [])


def _cfg():
    # Explicit values, so a developer's own .env (DRY_RUN=1, PLAYERS, ...)
    # can never change what these tests see.
    return config.Config(
        client_id="test",
        data_dir=Path("."),
        sleep_minutes=90,
        players=frozenset(),
        active_hours=None,
        dry_run=False,
        cooldown_seconds=120,
        connect_timeout_seconds=180,
        retry_seconds=30,
        log_level="INFO",
    )


if __name__ == "__main__":
    unittest.main()
