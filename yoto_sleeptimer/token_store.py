"""Read and atomically write the OAuth refresh token file (mode 0600)."""
from __future__ import annotations

import os
from pathlib import Path


def read(path: Path) -> str:
    try:
        token = path.read_text().strip()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"no refresh token at {path}. Run the login step first (see the README).") from None
    if not token:
        raise ValueError(f"{path} is empty. Run the login step again (see the README).")
    return token


def write(path: Path, refresh_token: str) -> None:
    """Write next to the target and rename over it, so a crash mid-write never
    leaves a half-written token. This needs the *directory* to be writable,
    which is why Docker mounts a whole data directory rather than one file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(refresh_token)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
