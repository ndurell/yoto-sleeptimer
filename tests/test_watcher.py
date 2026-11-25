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


class FakeClient:
    instance = None

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
        p1 = self.players["p1"]
        p1.last_event = NS(playback_status=PlaybackStatus.PLAYING, sleep_timer_active=False)
        await on_update(p1)
        await on_update(p1)  # burst: cooldown must swallow it
        p1.last_event = NS(playback_status=PlaybackStatus.PLAYING, sleep_timer_active=True)
        await on_update(p1)
        p2 = self.players["p2"]
        p2.last_event = NS(playback_status=PlaybackStatus.PLAYING, sleep_timer_active=False)
        await on_update(p2)  # filtered out by PLAYERS
        await on_disconnect(OSError("bye"))


class RunOnce(unittest.TestCase):
    def setUp(self):
        api = types.ModuleType("yoto_api")
        api.YotoClient = FakeClient
        models = types.ModuleType("yoto_api.models")
        models.PlaybackStatus = PlaybackStatus
        self._saved = {k: sys.modules.get(k) for k in ("yoto_api", "yoto_api.models")}
        sys.modules.update({"yoto_api": api, "yoto_api.models": models})

    def tearDown(self):
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


def _cfg():
    import os
    os.environ["YOTO_CLIENT_ID"] = "test"
    return config.load()


if __name__ == "__main__":
    unittest.main()
