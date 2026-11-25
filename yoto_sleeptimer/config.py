"""Settings, read from environment variables (and an optional .env file when
running outside Docker)."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .logic import ActiveHours, parse_active_hours, parse_player_filter

PLACEHOLDER_CLIENT_ID = "your-client-id-here"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    client_id: str
    data_dir: Path
    sleep_minutes: int
    players: frozenset[str]
    active_hours: Optional[ActiveHours]
    dry_run: bool
    cooldown_seconds: int
    connect_timeout_seconds: int
    retry_seconds: int
    log_level: str

    @property
    def token_file(self) -> Path:
        return self.data_dir / ".refresh_token"


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader for non-Docker runs. Real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()  # inline comment
        os.environ.setdefault(key.strip(), value)


def _int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    return value


def _bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def load(require_client_id: bool = True) -> Config:
    _load_dotenv(Path.cwd() / ".env")
    if hasattr(time, "tzset"):
        time.tzset()  # apply a TZ that came from .env to local times and logs

    client_id = os.environ.get("YOTO_CLIENT_ID", "").strip()
    if require_client_id and (not client_id or client_id == PLACEHOLDER_CLIENT_ID):
        raise ConfigError(
            "YOTO_CLIENT_ID is not set. Create an app at https://dashboard.yoto.dev "
            "and put its client ID in .env (see the README).")

    try:
        active_hours = parse_active_hours(os.environ.get("ACTIVE_HOURS", ""))
    except ValueError as exc:
        raise ConfigError(str(exc)) from None

    return Config(
        client_id=client_id,
        data_dir=Path(os.environ.get("DATA_DIR", "").strip() or "./data"),
        sleep_minutes=_int("SLEEP_MINUTES", 90),
        players=parse_player_filter(os.environ.get("PLAYERS", "")),
        active_hours=active_hours,
        dry_run=_bool("DRY_RUN"),
        cooldown_seconds=_int("REARM_COOLDOWN_SECONDS", 120, minimum=0),
        connect_timeout_seconds=_int("CONNECT_TIMEOUT_SECONDS", 180),
        retry_seconds=_int("RETRY_SECONDS", 30),
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
