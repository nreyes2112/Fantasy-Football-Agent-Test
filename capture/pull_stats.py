"""Weekly nflverse stats pull (phase1-data-platform-design.md §2, §8's
"Full nflverse refresh" row -- a separate job from the crosswalk rebuild,
even though both run weekly and share the same .venv311/nflreadpy runtime).

Pulls player_stats (per-player weekly stats) and team_stats (per-team
weekly stats, used for get_team_context's plays/pass-rate). Writes into
TODAY's existing dated snapshot under raw/nflverse/, with its own manifest
(nflverse_stats_manifest.json) -- separate from both raw/manifest.json
(pull_daily.py, daily-only, already finalized by the time this runs) and
curated_manifest.json (pull_crosswalk.py's identity resolution), so neither
gets touched after the fact (§4 immutability).

Note: neither table's validation is currently folded into the GOLD marker
pull_crosswalk.py writes (that marker predates these pulls existing). Their
own row-count/schema checks are recorded here, in their own manifest, but a
snapshot's GOLD status should be read as "raw capture + crosswalk
validated," not yet "and nflverse stats too" -- a known scope gap, not a
silent gap.

Requires nflreadpy (Python >= 3.10) -- run under .venv311.

Usage:
    python -m capture.pull_stats
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from capture.config import LEAGUE_TIMEZONE, NFLVERSE_STATS_SEASONS, SCHEMA_ROOT, SNAPSHOT_ROOT
from capture.manifest_utils import git_commit, sha256_file
from capture.sources import nflverse

MIN_ROWS_PER_SEASON = 15000  # player_stats: observed ~19,400 rows/season at week-level; generous floor
MIN_TEAM_ROWS_PER_SEASON = 250  # team_stats: 32 teams x ~17-18 weeks = ~550-576; generous floor

# Not every column nflverse returns -- just enough to document what this
# project actually reasons about. Full column list is still written to
# parquet as-is (raw, exactly as pulled); this is documentation, not a filter.
_PLAYER_STATS_SCHEMA_NOTES = {
    "player_id": "canonical gsis_id -- ALREADY the crosswalk key, verified 2026-07-18; no join needed",
    "player_display_name": "player name",
    "position": "position",
    "team": "NFL team abbreviation for that game",
    "season": "season year",
    "week": "week number within season_type",
    "season_type": "REG or POST",
    "targets": "raw target count",
    "target_share": "player targets / team pass attempts -- matches phase1 §5's target_share metric directly",
    "air_yards_share": "matches phase1 §5's air_yards_share metric directly",
    "wopr": "weighted opportunity rating -- nflverse's own version of phase1 §5's weighted_opportunity metric",
    "racr": "receiver air conversion ratio",
    "passing_epa": "EPA on pass plays",
    "rushing_epa": "EPA on rush plays",
    "receiving_epa": "EPA on targets",
    "fantasy_points_ppr": "nflverse's OWN ppr scoring assumption -- NOT verified to match this league's exact settings (charter §5: 4pt passing TD); don't treat as this league's PPG without checking get_league_scoring()",
    "...": "145 columns total from nflreadpy's load_player_stats(); most of the rest are position-specific (K/DST/IDP) counting stats out of charter scope (§4) but kept as-pulled since raw data is never filtered",
}

_TEAM_STATS_SCHEMA_NOTES = {
    "team": "NFL team abbreviation -- normalized to this project's convention (nflverse's own 'LA' renamed to 'LAR', see capture/sources/nflverse.py)",
    "season": "season year",
    "week": "week number within season_type",
    "season_type": "REG or POST",
    "attempts": "team pass attempts that game -- used with carries for plays-per-game and pass rate",
    "carries": "team rush attempts that game",
    "passing_epa": "team EPA on pass plays",
    "rushing_epa": "team EPA on rush plays",
    "...": "133 columns total from nflreadpy's load_team_stats(); does NOT include Vegas season win totals "
    "(a betting-market product, no free source found -- D-006) or O-line rank (not a raw stat) -- "
    "get_team_context reports both as genuinely unavailable rather than guessing",
}


def _pull_and_validate(
    label: str, fetch_fn, seasons: list[int], min_rows_per_season: int, id_cols_required: list[str],
    schema_table: str, schema_notes: dict, raw_dir: Path
) -> tuple[dict, list[dict]]:
    print(f"[stats] pulling nflverse {label} for seasons {seasons}...")
    df = fetch_fn(seasons=seasons)

    out_path = raw_dir / f"{label}.parquet"
    df.to_parquet(out_path, index=False)

    checks = []
    for season in seasons:
        season_rows = len(df[df["season"] == season])
        checks.append(
            {
                "check": f"{label}_{season}_min_rows",
                "passed": bool(season_rows >= min_rows_per_season),
                "detail": f"{season_rows} rows (floor {min_rows_per_season})",
            }
        )
    for col in id_cols_required:
        missing = int(df[col].isna().sum())
        checks.append(
            {
                "check": f"{label}_{col}_non_null",
                "passed": missing == 0,
                "detail": f"{missing}/{len(df)} rows missing {col}",
            }
        )

    schema_dir = Path(SCHEMA_ROOT) / schema_table
    schema_dir.mkdir(parents=True, exist_ok=True)
    schema_path = schema_dir / "v1.json"
    if not schema_path.exists():
        schema_path.write_text(json.dumps({"table": schema_table, "version": "v1", "columns": schema_notes}, indent=2))

    file_entry = {
        "table": label,
        "path": str(out_path.relative_to(Path("."))),
        "row_count": len(df),
        "sha256": sha256_file(out_path),
        "schema_version": "v1",
    }
    print(f"[stats] wrote {len(df)} rows across {len(seasons)} season(s) to {out_path}")
    return file_entry, checks


def run() -> int:
    today = datetime.now(LEAGUE_TIMEZONE).strftime("%Y-%m-%d")
    snapshot_dir = Path(SNAPSHOT_ROOT) / today
    raw_dir = snapshot_dir / "raw" / "nflverse"

    if not (snapshot_dir / "manifest.json").exists():
        raise SystemExit(f"No raw snapshot for {today} yet -- run `python -m capture.pull_daily` (.venv) first.")
    raw_dir.mkdir(parents=True, exist_ok=True)

    player_file, player_checks = _pull_and_validate(
        "player_stats", nflverse.fetch_player_stats, NFLVERSE_STATS_SEASONS, MIN_ROWS_PER_SEASON,
        [],  # player_id nullness is checked specially below (team-level aggregate rows are expected)
        "nflverse_player_stats", _PLAYER_STATS_SCHEMA_NOTES, raw_dir,
    )
    # nflverse's own weekly player_stats includes a handful of team-level
    # aggregate rows per week (no player_name/position at all, just team) --
    # verified 2026-07-18: 44/38402 rows, one roughly per team per week.
    # These are a real, structural part of the source, not a bad pull -- the
    # check only requires player_id on rows that actually claim to be a player.
    player_df = pd.read_parquet(Path(player_file["path"]))
    player_rows = player_df[player_df["position"].notna()]
    missing_id = int(player_rows["player_id"].isna().sum())
    player_checks.append(
        {
            "check": "player_stats_player_id_non_null_for_player_rows",
            "passed": missing_id == 0,
            "detail": f"{missing_id}/{len(player_rows)} player-attributed rows missing player_id "
            f"({len(player_df) - len(player_rows)} team-level aggregate rows excluded from this check)",
        }
    )

    team_file, team_checks = _pull_and_validate(
        "team_stats", nflverse.fetch_team_stats, NFLVERSE_STATS_SEASONS, MIN_TEAM_ROWS_PER_SEASON,
        ["team"], "nflverse_team_stats", _TEAM_STATS_SCHEMA_NOTES, raw_dir,
    )

    all_checks = player_checks + team_checks
    all_passed = all(c["passed"] for c in all_checks)
    manifest = {
        "snapshot_date": today,
        "generated_at": datetime.now(LEAGUE_TIMEZONE).isoformat(),
        "code_git_commit": git_commit(),
        "seasons_pulled": NFLVERSE_STATS_SEASONS,
        "source_endpoints": {
            "player_stats": "nflreadpy.load_player_stats(summary_level='week')",
            "team_stats": "nflreadpy.load_team_stats(summary_level='week')",
        },
        "files": [player_file, team_file],
        "validation": {
            "stage": "stage1_lite (row-count + non-null id sanity; not yet folded into pull_crosswalk.py's GOLD marker)",
            "checks": all_checks,
            "all_passed": all_passed,
        },
    }
    manifest_path = snapshot_dir / "nflverse_stats_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"[stats] wrote {manifest_path} (all_checks_passed={all_passed})")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(run())
