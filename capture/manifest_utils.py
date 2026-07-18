"""Small helpers shared by every script that writes a manifest file
(pull_daily.py's SnapshotWriter, pull_crosswalk.py, pull_stats.py)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        )
        return out.stdout.strip()
    except Exception:
        return None
