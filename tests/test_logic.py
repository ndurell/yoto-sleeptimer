import datetime as dt
import os
import stat
import tempfile
import unittest
from pathlib import Path

from yoto_sleeptimer import token_store
from yoto_sleeptimer.logic import (
    parse_active_hours, parse_player_filter, player_selected, should_arm)

T = dt.time


def arm(**overrides):
    args = dict(playing=True, sleep_timer_active=False, now_monotonic=1000.0,
                last_arm_monotonic=None, cooldown_seconds=120,
                local_time=T(20, 0), active_hours=None)
    args.update(overrides)
    return should_arm(**args)


class ShouldArm(unittest.TestCase):
    def test_arms_when_playing_without_timer(self):
        self.assertTrue(arm())

    def test_not_when_paused_or_stopped(self):
        self.assertFalse(arm(playing=False))

    def test_leaves_existing_timer_alone(self):
        self.assertFalse(arm(sleep_timer_active=True))

    def test_unknown_timer_state_still_arms(self):
        self.assertFalse(arm(sleep_timer_active=None))

    def test_cooldown(self):
        self.assertFalse(arm(last_arm_monotonic=950.0))
        self.assertTrue(arm(last_arm_monotonic=800.0))

    def test_active_hours(self):
        night = parse_active_hours("18:00-07:00")
        self.assertTrue(arm(active_hours=night, local_time=T(23, 30)))
        self.assertTrue(arm(active_hours=night, local_time=T(6, 59)))
        self.assertFalse(arm(active_hours=night, local_time=T(7, 0)))
        self.assertFalse(arm(active_hours=night, local_time=T(12, 0)))


class Parsing(unittest.TestCase):
    def test_active_hours(self):
        self.assertIsNone(parse_active_hours(""))
        day = parse_active_hours("09:00-17:00")
        self.assertTrue(day.contains(T(9, 0)))
        self.assertFalse(day.contains(T(17, 0)))
        self.assertTrue(parse_active_hours("00:00-00:00").contains(T(3, 0)))
        with self.assertRaises(ValueError):
            parse_active_hours("7pm-7am")

    def test_player_filter(self):
        sel = parse_player_filter(" Bedroom , ABC123,")
        self.assertTrue(player_selected(sel, "x", "bedroom"))
        self.assertTrue(player_selected(sel, "abc123", "Other"))
        self.assertFalse(player_selected(sel, "zzz", "Kitchen"))
        self.assertTrue(player_selected(frozenset(), "zzz", None))


class DotEnv(unittest.TestCase):
    def test_utf8_quotes_and_inline_comments(self):
        from yoto_sleeptimer.config import _load_dotenv
        keys = ("YST_A", "YST_B", "YST_C", "YST_D")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text("# comment\nYST_A=B\u00e9b\u00e9\nYST_B=Europe/London   # zone\n"
                            "YST_C='has # hash'\nYST_D=a#b\n", encoding="utf-8")
            for k in keys:
                os.environ.pop(k, None)
            try:
                _load_dotenv(path)
                self.assertEqual([os.environ[k] for k in keys],
                                 ["B\u00e9b\u00e9", "Europe/London", "has # hash", "a#b"])
            finally:
                for k in keys:
                    os.environ.pop(k, None)


class TokenStore(unittest.TestCase):
    def test_atomic_write_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / ".refresh_token"
            token_store.write(path, "abc")
            token_store.write(path, "def")
            self.assertEqual(token_store.read(path), "def")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(os.listdir(path.parent), [".refresh_token"])

    def test_missing_or_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".refresh_token"
            with self.assertRaises(FileNotFoundError):
                token_store.read(path)
            path.write_text("\n")
            with self.assertRaises(ValueError):
                token_store.read(path)


if __name__ == "__main__":
    unittest.main()
