"""Snapshot pinning for the agent access layer (phase1-data-platform-design.md §7).

"Agents read ONLY through the serve layer, pinned to one gold snapshot per
run" -- this module is where that pin is enforced. No function here ever
falls back to a non-GOLD snapshot silently; if no GOLD snapshot exists, or
the requested date isn't GOLD, callers get a clear error instead of
quietly-wrong data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from capture.config import SCHEMA_ROOT, SNAPSHOT_ROOT


class NoGoldSnapshotError(RuntimeError):
    pass


def gold_snapshot_dates(snapshot_root: str = SNAPSHOT_ROOT) -> list[str]:
    root = Path(snapshot_root)
    if not root.exists():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and (d / "GOLD").exists())


def latest_gold_snapshot(snapshot_root: str = SNAPSHOT_ROOT) -> str:
    dates = gold_snapshot_dates(snapshot_root)
    if not dates:
        raise NoGoldSnapshotError(
            "No GOLD-marked snapshot exists yet. Run `python -m capture.pull_daily` (.venv) "
            "then `python -m capture.pull_crosswalk` (.venv311) to produce one."
        )
    return dates[-1]


def resolve_snapshot_date(snapshot_date: str | None, snapshot_root: str = SNAPSHOT_ROOT) -> str:
    """None -> latest GOLD snapshot. An explicit date must itself be GOLD --
    this is what lets Phase 2's backtest harness pin to a frozen world later
    without silently reading unvalidated data."""
    if snapshot_date is None:
        return latest_gold_snapshot(snapshot_root)
    gold_path = Path(snapshot_root) / snapshot_date / "GOLD"
    if not gold_path.exists():
        raise NoGoldSnapshotError(f"{snapshot_date} is not GOLD-marked (or doesn't exist) -- refusing to serve it.")
    return snapshot_date


def load_curated_table(snapshot_date: str, table: str, snapshot_root: str = SNAPSHOT_ROOT) -> pd.DataFrame:
    path = Path(snapshot_root) / snapshot_date / "curated" / f"{table}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return pd.read_parquet(path)


def load_raw_table(snapshot_date: str, source: str, table: str, snapshot_root: str = SNAPSHOT_ROOT) -> pd.DataFrame:
    path = Path(snapshot_root) / snapshot_date / "raw" / source / f"{table}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return pd.read_parquet(path)


def schema_version(table: str, schema_root: str = SCHEMA_ROOT) -> str | None:
    path = Path(schema_root) / table / "v1.json"
    if not path.exists():
        return None
    return json.loads(path.read_text()).get("version")
