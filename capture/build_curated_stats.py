"""Builds the actual analysis-ready curated layer (phase1-data-platform-
design.md §1's architecture diagram: raw -> STORE(curated, "rebuilt from
raw by versioned code") -> SERVE). Until this script, `curated/` only held
crosswalk identity-resolution tables (sleeper_resolved, espn_resolved,
etc.) -- useful, but not the actual "curated/weekly_stats" table §5's data
dictionary literally names as several metrics' source_tables.

Joins player_stats (already gsis_id-keyed) with snap_counts_resolved's
offense_pct (renamed to snap_share, matching phase1 §5's metric name) on
(gsis_id, season, week) -- verified 2026-07-18 that this key has zero
duplicates in snap_counts_resolved, so the join can't silently fan out
rows. Does NOT bake in derived ratio metrics (aDOT, EPA_per_target,
TD_rate) as columns here -- those need window-level sum-of-ratios, not a
per-game average of per-game ratios, which would be a subtly wrong number
for any multi-game request. access/metrics.py remains the single source of
truth for those; this table only adds columns that are correct at any
grain (a raw count and a directly-reported percentage).

Runs after both pull_daily.py (raw) and pull_crosswalk.py (identity
resolution + snap_counts) in the weekly workflow -- depends on both. Writes
its own manifest (curated_stats_manifest.json) rather than appending to
curated_manifest.json, since a different script owns that file's write
(§4: nothing under a dated snapshot's already-written files is edited
after the fact by a different process, to avoid a race between the two
scripts running out of order).

Requires nflreadpy's Python >= 3.10 runtime only indirectly (reads
parquet already written by other scripts) -- runs fine under .venv or
.venv311, but the workflow runs it under .venv311 since it follows
pull_crosswalk.py in the same job.

Usage:
    python -m capture.build_curated_stats
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from capture.config import LEAGUE_TIMEZONE, SCHEMA_ROOT, SNAPSHOT_ROOT
from capture.manifest_utils import git_commit, sha256_file

_SCHEMA_NOTES = {
    "gsis_id": "canonical player id (player_stats' native key)",
    "season": "season year",
    "week": "week number",
    "snap_share": "this player's share of the team's offensive snaps that game -- joined in from snap_counts_resolved.offense_pct; null where snap_counts has no matching (gsis_id, season, week) row",
    "...": "plus every column from raw/nflverse/player_stats.parquet, unchanged -- this table adds snap_share, it doesn't replace or filter player_stats",
}


def run() -> int:
    today = datetime.now(LEAGUE_TIMEZONE).strftime("%Y-%m-%d")
    snapshot_dir = Path(SNAPSHOT_ROOT) / today
    raw_dir = snapshot_dir / "raw"
    curated_dir = snapshot_dir / "curated"

    player_stats_path = raw_dir / "nflverse" / "player_stats.parquet"
    snap_counts_path = curated_dir / "snap_counts_resolved.parquet"
    if not player_stats_path.exists():
        raise SystemExit(f"No {player_stats_path} yet -- run `python -m capture.pull_stats` (.venv311) first.")
    if not snap_counts_path.exists():
        raise SystemExit(f"No {snap_counts_path} yet -- run `python -m capture.pull_crosswalk` (.venv311) first.")

    print("[curated-stats] loading player_stats + snap_counts_resolved...")
    player_stats = pd.read_parquet(player_stats_path)
    snap_counts = pd.read_parquet(snap_counts_path)

    snap_share_by_key = (
        snap_counts.dropna(subset=["gsis_id"])[["gsis_id", "season", "week", "offense_pct"]]
        .rename(columns={"gsis_id": "player_id", "offense_pct": "snap_share"})
    )
    weekly_stats = player_stats.merge(snap_share_by_key, on=["player_id", "season", "week"], how="left")

    if len(weekly_stats) != len(player_stats):
        raise SystemExit(
            f"BUG: join fanned out rows ({len(player_stats)} -> {len(weekly_stats)}) -- "
            "snap_counts_resolved must have duplicate (gsis_id, season, week) keys; investigate before trusting this table"
        )

    curated_dir.mkdir(parents=True, exist_ok=True)
    out_path = curated_dir / "weekly_stats.parquet"
    weekly_stats.to_parquet(out_path, index=False)

    schema_dir = Path(SCHEMA_ROOT) / "weekly_stats"
    schema_dir.mkdir(parents=True, exist_ok=True)
    schema_path = schema_dir / "v1.json"
    if not schema_path.exists():
        schema_path.write_text(json.dumps({"table": "weekly_stats", "version": "v1", "columns": _SCHEMA_NOTES}, indent=2))

    player_rows = weekly_stats[weekly_stats["position"].notna()]
    snap_share_coverage = round(100.0 * player_rows["snap_share"].notna().sum() / len(player_rows), 2) if len(player_rows) else 0.0
    checks = [
        {
            "check": "weekly_stats_row_count_matches_player_stats",
            "passed": len(weekly_stats) == len(player_stats),
            "detail": f"{len(weekly_stats)} rows (player_stats had {len(player_stats)})",
        },
        {
            "check": "weekly_stats_snap_share_reasonable_coverage",
            "passed": bool(snap_share_coverage >= 50.0),
            "detail": f"{snap_share_coverage}% of player-attributed rows have a snap_share value "
            "(not 100% expected -- snap_counts_resolved itself resolves ~81% of its rows to gsis_id)",
        },
    ]
    all_passed = all(c["passed"] for c in checks)

    manifest = {
        "snapshot_date": today,
        "generated_at": datetime.now(LEAGUE_TIMEZONE).isoformat(),
        "code_git_commit": git_commit(),
        "built_from": [str(player_stats_path.relative_to(Path("."))), str(snap_counts_path.relative_to(Path(".")))],
        "files": [
            {
                "table": "weekly_stats",
                "path": str(out_path.relative_to(Path("."))),
                "row_count": len(weekly_stats),
                "sha256": sha256_file(out_path),
                "schema_version": "v1",
            }
        ],
        "validation": {"checks": checks, "all_passed": all_passed},
    }
    manifest_path = snapshot_dir / "curated_stats_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"[curated-stats] wrote {len(weekly_stats)} rows to {out_path}")
    print(f"[curated-stats] snap_share coverage on player rows: {snap_share_coverage}%")
    print(f"[curated-stats] wrote {manifest_path} (all_checks_passed={all_passed})")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(run())
