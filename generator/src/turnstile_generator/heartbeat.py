"""Heartbeat file for the container health check: touched while the process makes progress."""

import time
from pathlib import Path


def touch(path: Path) -> None:
    path.touch()


def is_fresh(path: Path, max_age_s: float, now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except OSError:
        return False
    return age <= max_age_s
