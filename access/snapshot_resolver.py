"""Snapshot pinning for the agent access layer (phase1-data-platform-design.md §7),
plus the frozen-world pin (phase2-backtest-harness-design.md §2's "candidates
call the SAME Phase 1 access layer, just pinned to a frozen snapshot").

"Agents read ONLY through the serve layer, pinned to one gold snapshot per
run" -- this module is where that pin is enforced. No function here ever
falls back to a non-GOLD snapshot silently; if no GOLD snapshot exists, or
the requested date isn't GOLD, callers get a clear error instead of
quietly-wrong data.

Frozen-world pin: setting the FROZEN_WORLD_PIN environment variable to a
world date (e.g. "2025-06-18") flips the whole access layer into frozen-world
serving for that process AND any subprocess it spawns -- an env var rather
than module state because backtest agent runs invoke tools as separate
`python -c` processes, and the pin must survive that boundary without the
agent having to (or being able to) manage it. While pinned:
  - a world is only served if its leakage audit exists and passed, the
    frozen-world mirror of the GOLD gate;
  - resolve_snapshot_date() REFUSES every request -- no code path in a
    pinned process can reach live `data/snapshots/` data by accident.
    Frozen-mode serving code loads what it needs explicitly via
    load_world_table() / physical_gold_snapshot_for_world().
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from capture.config import SCHEMA_ROOT, SNAPSHOT_ROOT

FROZEN_WORLD_PIN_ENV = "FROZEN_WORLD_PIN"
FROZEN_WORLDS_ROOT = "backtest/frozen_worlds"


class NoGoldSnapshotError(RuntimeError):
    pass


class FrozenWorldError(RuntimeError):
    pass


def pinned_world(frozen_root: str = FROZEN_WORLDS_ROOT) -> str | None:
    """The active frozen-world pin, or None for normal (live GOLD) serving.
    A pinned world must exist and have a PASSING leakage audit on disk --
    the frozen-world equivalent of refusing non-GOLD snapshots."""
    world = os.environ.get(FROZEN_WORLD_PIN_ENV)
    if world is None:
        return None
    world_dir = Path(frozen_root) / world
    if not world_dir.is_dir():
        raise FrozenWorldError(
            f"{FROZEN_WORLD_PIN_ENV}={world!r} but no such world under {frozen_root}/ -- "
            f"available: {sorted(d.name for d in Path(frozen_root).iterdir() if d.is_dir())}"
        )
    audit_path = world_dir / "leakage_audit.json"
    if not audit_path.exists():
        raise FrozenWorldError(
            f"world {world} has no leakage_audit.json -- run `python -m backtest.leakage_audit --world {world}` "
            "before serving it (mirror of the GOLD gate: unaudited worlds are never served)"
        )
    audit = json.loads(audit_path.read_text())
    if not audit.get("all_automated_checks_passed"):
        raise FrozenWorldError(f"world {world}'s leakage audit did NOT pass -- refusing to serve it")
    return world


def world_season(world_date: str) -> int:
    """The outcome season a frozen world predicts. By construction (D-009,
    baselines.WORLD_TO_SEASON) a world sits mid-year BEFORE its own year's
    season is played, so the world date's year IS the outcome season."""
    return int(world_date[:4])


def load_world_table(world_date: str, source: str, table: str, frozen_root: str = FROZEN_WORLDS_ROOT) -> pd.DataFrame:
    path = Path(frozen_root) / world_date / "raw" / source / f"{table}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return pd.read_parquet(path)


def physical_gold_snapshot_for_world() -> str:
    """Frozen-mode serving of season-stats content (final, immutable
    facts about seasons completed BEFORE the world date) physically reads the
    latest GOLD snapshot's tables and filters to pre-world seasons. This is
    the one sanctioned live-snapshot read in a pinned process; everything
    else must go through load_world_table()."""
    return latest_gold_snapshot()


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
    """None -> latest GOLD snapshot. An explicit date must itself be GOLD.
    In a world-pinned process this function refuses EVERY request: the
    frozen-mode branches in access/layer.py return before ever calling it,
    so any code path that reaches here while pinned is by definition about
    to read live data inside a backtest -- fail loudly instead."""
    world = pinned_world()
    if world is not None:
        raise FrozenWorldError(
            f"process is pinned to frozen world {world} ({FROZEN_WORLD_PIN_ENV} is set) -- live snapshot "
            f"resolution is refused; frozen-mode serving must use load_world_table()/physical_gold_snapshot_for_world()"
        )
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
