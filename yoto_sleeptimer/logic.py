"""Pure decision logic, kept free of any network or Yoto library imports so it
is easy to unit test."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ActiveHours:
    """A daily time window, e.g. 18:00-07:00. May wrap past midnight."""

    start: dt.time
    end: dt.time

    def contains(self, t: dt.time) -> bool:
        if self.start == self.end:
            return True  # "00:00-00:00" style: all day
        if self.start < self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end  # wraps past midnight


def parse_active_hours(value: str) -> Optional[ActiveHours]:
    """Parse "HH:MM-HH:MM". Empty means "always" and returns None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        start_s, end_s = (part.strip() for part in value.split("-", 1))
        start = dt.datetime.strptime(start_s, "%H:%M").time()
        end = dt.datetime.strptime(end_s, "%H:%M").time()
    except ValueError:
        raise ValueError(
            f"ACTIVE_HOURS must look like 18:00-07:00, got {value!r}") from None
    return ActiveHours(start, end)


def parse_player_filter(value: str) -> frozenset[str]:
    """Comma-separated player names or device ids, compared case-insensitively.
    Empty means every player on the account."""
    return frozenset(p.strip().lower() for p in (value or "").split(",") if p.strip())


def player_selected(selected: frozenset[str], device_id: str, name: str | None) -> bool:
    if not selected:
        return True
    return device_id.lower() in selected or (name or "").lower() in selected


def should_arm(
    *,
    playing: bool,
    sleep_timer_active: Optional[bool],
    now_monotonic: float,
    last_arm_monotonic: Optional[float],
    cooldown_seconds: float,
    local_time: dt.time,
    active_hours: Optional[ActiveHours],
) -> bool:
    """Decide whether to arm a sleep timer for one player right now.

    - Only while the player is playing.
    - Never when a sleep timer is already running (for example one set by
      hand in the Yoto app): that timer is left alone.
    - Never while the timer state is unknown (``None``): the player hasn't
      reported it yet, and arming could replace a timer set by hand. The
      watcher waits for a report and decides what to do if none comes.
    - Not again within ``cooldown_seconds`` of our last arm on the same
      player, because one press of play can produce a burst of events.
    - Only inside ``active_hours``, if a window is configured.
    """
    if not playing:
        return False
    if sleep_timer_active is not False:
        return False
    if last_arm_monotonic is not None and now_monotonic - last_arm_monotonic < cooldown_seconds:
        return False
    if active_hours is not None and not active_hours.contains(local_time):
        return False
    return True
