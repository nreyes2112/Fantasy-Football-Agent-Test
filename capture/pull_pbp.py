"""Red-zone / rush-type pbp pull (D-017) -- closes the specific data gap
Agent 1's opportunity/volume methodology named in its own output twice
(D-015/D-016 QB and RB/WR/TE runs): "weighted opportunity ... red-zone and
end-zone usage weighted up" and "designed-run vs scramble split... not
retrievable (needs nflverse pbp, not pulled)".

DEVIATION from pull_stats.py's "raw is never filtered" precedent: full
play-by-play is ~20.7MB/season (372 columns) vs. ~0.2MB for the aggregated
tables this project actually needs -- writing two full seasons of raw pbp
(~40MB) into this project's git-tracked data/snapshots/ tree was judged not
worth the repo bloat for a handful of derived counts. This script pulls pbp
into memory, aggregates immediately (capture/sources/nflverse.py's
fetch_redzone_pbp_summary), and writes ONLY the aggregated player/team
tables -- there is no raw/nflverse/pbp.parquet. Writes directly to curated/
(not raw/) for this reason; owns its own manifest (pbp_manifest.json), never
touching raw/manifest.json, curated_manifest.json, nflverse_stats_manifest.json,
or curated_stats_manifest.json.

Requires nflreadpy (Python >= 3.10) -- run under .venv311.

Usage:
    python -m capture.pull_pbp
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from capture.config import LEAGUE_TIMEZONE, NFLVERSE_STATS_SEASONS, SCHEMA_ROOT, SNAPSHOT_ROOT
from capture.manifest_utils import git_commit, sha256_file
from capture.sources import nflverse

MIN_PLAYER_ROWS_PER_SEASON = 300  # observed ~500-700 player-game rows/season with any red-zone involvement; generous floor
MIN_TEAM_ROWS_PER_SEASON = 250  # 32 teams x ~17-18 weeks, same floor as team_stats

_PLAYER_SCHEMA_NOTES = {
    "player_id": "canonical gsis_id (verified 2026-07-19: pbp's rusher/receiver_player_id are already gsis_id, no crosswalk join needed)",
    "season": "season year",
    "week": "week number (REG only -- pull_pbp filters season_type == 'REG', matching ground_truth.py/charter §3)",
    "team": "posteam, normalized LA->LAR per this project's convention (capture/sources/nflverse.py)",
    "rz_targets": "pass attempts targeting this player with yardline_100 <= 20",
    "rz_pass_tds": "of rz_targets, how many were touchdowns",
    "rz_carries": "rush attempts by this player with yardline_100 <= 20",
    "rz_rush_tds": "of rz_carries, how many were touchdowns",
    "designed_carries": "ALL non-red-zone-restricted rush attempts by this player where qb_scramble != 1 and qb_kneel != 1 (season-long run-call profile, not red-zone-only)",
    "scramble_carries": "same scope as designed_carries but qb_scramble == 1 -- nonzero only for QBs by construction",
}
_TEAM_SCHEMA_NOTES = {
    "season": "season year", "week": "week number (REG only)",
    "team": "posteam, normalized LA->LAR",
    "team_rz_pass_attempts": "team pass attempts with yardline_100 <= 20 -- red_zone_target_share denominator",
    "team_rz_rush_attempts": "team rush attempts with yardline_100 <= 20 -- red_zone_carry_share denominator",
}


def _target_snapshot_date(date: str | None = None) -> str:
    """The latest snapshot that has the prerequisite stats (or an explicit
    --date). pbp for 2024/2025 (completed seasons) is time-invariant, so
    there's no correctness reason to gate this backfill behind a brand-new
    same-day raw capture run existing
    first; forcing one would mean touching Sleeper/FFC/ESPN's live APIs
    (network + credentials) purely to satisfy a directory-naming convention
    for data that hasn't changed.

    Gates on the PREREQUISITE (raw/nflverse/player_stats.parquet), not just any
    raw manifest: the red-zone tables get joined onto player_stats by
    build_curated_stats.py, so they must land in a snapshot that actually has
    player_stats. This matters because a daily-only capture (e.g. a weekday
    that ran pull_daily but not the weekly pull_stats) HAS a raw manifest but
    NO player_stats -- targeting it would strand the red-zone tables in a
    snapshot nothing joins them into. An explicit `date` overrides the search."""
    root = Path(SNAPSHOT_ROOT)
    if date is not None:
        if not (root / date / "raw" / "nflverse" / "player_stats.parquet").exists():
            raise SystemExit(f"--date {date} has no raw/nflverse/player_stats.parquet -- run `python -m capture.pull_stats` for it first.")
        return date
    with_stats = sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "raw" / "nflverse" / "player_stats.parquet").exists()
    )
    if not with_stats:
        raise SystemExit("No snapshot has raw/nflverse/player_stats.parquet yet -- run `python -m capture.pull_stats` (.venv311) first.")
    return with_stats[-1]


def run(date: str | None = None) -> int:
    target_date = _target_snapshot_date(date)
    snapshot_dir = Path(SNAPSHOT_ROOT) / target_date
    curated_dir = snapshot_dir / "curated"
    curated_dir.mkdir(parents=True, exist_ok=True)

    print(f"[pbp] pulling + aggregating play-by-play for seasons {NFLVERSE_STATS_SEASONS} (in-memory only, not persisted raw)...")
    player_df, team_df = nflverse.fetch_redzone_pbp_summary(NFLVERSE_STATS_SEASONS)

    player_path = curated_dir / "redzone_player_stats.parquet"
    team_path = curated_dir / "redzone_team_stats.parquet"
    player_df.to_parquet(player_path, index=False)
    team_df.to_parquet(team_path, index=False)

    checks = []
    for season in NFLVERSE_STATS_SEASONS:
        p_rows = len(player_df[player_df["season"] == season])
        t_rows = len(team_df[team_df["season"] == season])
        checks.append({"check": f"redzone_player_stats_{season}_min_rows", "passed": bool(p_rows >= MIN_PLAYER_ROWS_PER_SEASON), "detail": f"{p_rows} rows (floor {MIN_PLAYER_ROWS_PER_SEASON})"})
        checks.append({"check": f"redzone_team_stats_{season}_min_rows", "passed": bool(t_rows >= MIN_TEAM_ROWS_PER_SEASON), "detail": f"{t_rows} rows (floor {MIN_TEAM_ROWS_PER_SEASON})"})
    missing_player_id = int(player_df["player_id"].isna().sum())
    checks.append({"check": "redzone_player_stats_player_id_non_null", "passed": missing_player_id == 0, "detail": f"{missing_player_id}/{len(player_df)} rows missing player_id"})

    for table, notes in [("redzone_player_stats", _PLAYER_SCHEMA_NOTES), ("redzone_team_stats", _TEAM_SCHEMA_NOTES)]:
        schema_dir = Path(SCHEMA_ROOT) / table
        schema_dir.mkdir(parents=True, exist_ok=True)
        schema_path = schema_dir / "v1.json"
        if not schema_path.exists():
            schema_path.write_text(json.dumps({"table": table, "version": "v1", "columns": notes}, indent=2))

    all_passed = all(c["passed"] for c in checks)
    manifest = {
        "snapshot_date": target_date,
        "generated_at": datetime.now(LEAGUE_TIMEZONE).isoformat(),
        "code_git_commit": git_commit(),
        "seasons_pulled": NFLVERSE_STATS_SEASONS,
        "source_endpoint": "nflreadpy.load_pbp() -- aggregated in-memory, full pbp never persisted (D-017 deviation, see capture/pull_pbp.py docstring)",
        "files": [
            {"table": "redzone_player_stats", "path": str(player_path.relative_to(Path("."))), "row_count": len(player_df), "sha256": sha256_file(player_path), "schema_version": "v1"},
            {"table": "redzone_team_stats", "path": str(team_path.relative_to(Path("."))), "row_count": len(team_df), "sha256": sha256_file(team_path), "schema_version": "v1"},
        ],
        "validation": {"checks": checks, "all_passed": all_passed},
    }
    manifest_path = snapshot_dir / "pbp_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"[pbp] wrote {len(player_df)} player rows, {len(team_df)} team rows")
    print(f"[pbp] wrote {manifest_path} (all_checks_passed={all_passed})")
    return 0 if all_passed else 1


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="snapshot date to backfill (default: latest with player_stats)")
    return run(parser.parse_args().date)


if __name__ == "__main__":
    sys.exit(main())
