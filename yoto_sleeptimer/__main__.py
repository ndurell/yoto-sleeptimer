"""Command line: ``python -m yoto_sleeptimer [run|login [--force]|check]``."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="yoto-sleeptimer",
        description="Automatically arm a sleep timer whenever a Yoto player starts playing.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="watch the players (default)")
    login_p = sub.add_parser("login", help="sign in to Yoto and save a refresh token")
    login_p.add_argument("--force", action="store_true", help="replace an existing token")
    sub.add_parser("check", help="validate settings and exit")
    args = parser.parse_args(argv)
    command = args.command or "run"

    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(level=cfg.log_level,
                        format="%(asctime)s %(levelname)s %(message)s")

    if command == "check":
        print(f"Client ID:     set")
        print(f"Token file:    {cfg.token_file} "
              f"({'present' if cfg.token_file.exists() else 'missing, run login'})")
        print(f"Sleep timer:   {cfg.sleep_minutes} min")
        print(f"Players:       {', '.join(sorted(cfg.players)) or 'all'}")
        hours = cfg.active_hours
        print(f"Active hours:  {hours.start:%H:%M}-{hours.end:%H:%M}" if hours else
              "Active hours:  always")
        print(f"Dry run:       {'yes' if cfg.dry_run else 'no'}")
        return 0

    try:
        if command == "login":
            from .login import login
            return asyncio.run(login(cfg, force=args.force))
        from .watcher import run_forever
        asyncio.run(run_forever(cfg))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
