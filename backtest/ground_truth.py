"""Ground-truth builder (docs/phase2-backtest-harness-design.md §2, build-order
step 2) -- actual end-of-season finishes for the 2024 and 2025 seasons under
this league's EXACT scoring rules (charter.md §5), independent of any frozen
world or candidate system.

Deliberately unblocked ahead of frozen-world assembly: ground truth is built
FROM completed seasons, so unlike ADP/roster/depth-chart data it carries no
lookahead-bias risk by construction -- there is no "as of" date to violate.
It reuses nflverse player_stats already pulled into the one real GOLD
snapshot (raw/nflverse/player_stats.parquet, capture/pull_stats.py) and the
same charter-accurate scoring function access/metrics.py verified against
real 2025 week-1 stat lines (Josh Allen, Jahmyr Gibbs) -- so ground truth and
the flagship PPG metric are computed by the same formula, not two versions
that could silently drift apart.

Storage is deliberately OUTSIDE data/snapshots/ (which access/snapshot_resolver.py
is the only sanctioned read path into, for candidates) and outside the
frozen_worlds/ directory Phase 2 will build next -- per the design doc's
leakage checklist: "Ground truth stored outside the world directory with
access denied to candidate runs." Nothing in access/ or backtest/frozen_worlds
(once it exists) should ever import this module's output.

Usage (either venv works -- this module only reads an already-pulled parquet
file via pandas, no nflreadpy dependency):
    python -m backtest.ground_truth               # both charter frozen-world seasons (2024, 2025)
    python -m backtest.ground_truth --season 2025  # one season only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from access.metrics import compute_fantasy_points, league_scoring_by_column
from access.snapshot_resolver import load_raw_table, resolve_snapshot_date, schema_version
from capture.config import LEAGUE_TIMEZONE, NFLVERSE_STATS_SEASONS
from capture.manifest_utils import git_commit, sha256_file

# charter.md §3: "Games threshold for PPG scoring: minimum 8 games played."
GAMES_THRESHOLD = 8
# charter.md §4: v1 scope is QB/RB/WR/TE only (K/DST/IDP out of scope).
CHARTER_POSITIONS = ("QB", "RB", "WR", "TE")

GROUND_TRUTH_ROOT = Path("backtest/ground_truth")


def build_positional_finishes(season: int, snapshot_date: str | None = None) -> pd.DataFrame:
    """One row per gsis_id who played >= GAMES_THRESHOLD regular-season games
    at a charter-in-scope position in `season`, with actual season PPG under
    this league's exact scoring rules and a positional rank (1 = best PPG).

    Regular season only (season_type == "REG") -- postseason stats would let
    a player accrue extra games/points a redraft league never sees, and
    playoff participation itself correlates with team quality in a way that
    would quietly bias the "actual finish" this is supposed to measure.
    """
    pinned_date = resolve_snapshot_date(snapshot_date)
    df = load_raw_table(pinned_date, "nflverse", "player_stats")

    scoring_by_column = league_scoring_by_column()
    if scoring_by_column is None:
        raise RuntimeError(
            "get_league_scoring() is unavailable -- run `python -m capture.espn_settings_check` (.venv) first."
        )

    season_rows = df[
        (df["season"] == season) & (df["season_type"] == "REG") & (df["position"].isin(CHARTER_POSITIONS))
    ]
    if len(season_rows) == 0:
        raise ValueError(f"no REG-season {CHARTER_POSITIONS} rows found for season={season} in {pinned_date}'s snapshot")

    records = []
    for gsis_id, player_rows in season_rows.groupby("player_id"):
        games_played = len(player_rows)
        if games_played < GAMES_THRESHOLD:
            continue
        summed = player_rows.sum(numeric_only=True)
        total_points = compute_fantasy_points(summed, scoring_by_column)
        records.append(
            {
                "gsis_id": gsis_id,
                "player_name": player_rows["player_display_name"].iloc[0],
                # position: mode across the season's rows -- a player's position tag rarely
                # changes mid-season, but this guards against any stray row-level noise.
                "position": player_rows["position"].mode().iloc[0],
                "team": player_rows.sort_values("week")["team"].iloc[-1],  # most recent team that season
                "season": season,
                "games_played": games_played,
                "total_fantasy_points": total_points,
                "ppg": round(total_points / games_played, 2),
            }
        )

    finishes = pd.DataFrame.from_records(records)
    # Positional rank 1 = best PPG. method="min" (standard competition ranking:
    # ties share the better rank, next rank skips) -- not specified by Appendix M,
    # documented here as the build-time tie-break choice.
    finishes["positional_rank"] = (
        finishes.groupby("position")["ppg"].rank(method="min", ascending=False).astype(int)
    )
    return finishes.sort_values(["position", "positional_rank"]).reset_index(drop=True)


def write_ground_truth(season: int, snapshot_date: str | None = None) -> Path:
    pinned_date = resolve_snapshot_date(snapshot_date)
    finishes = build_positional_finishes(season, snapshot_date)

    season_dir = GROUND_TRUTH_ROOT / str(season)
    season_dir.mkdir(parents=True, exist_ok=True)
    out_path = season_dir / "positional_finishes.parquet"
    finishes.to_parquet(out_path, index=False)

    manifest = {
        "season": season,
        "generated_at": datetime.now(LEAGUE_TIMEZONE).isoformat(),
        "code_git_commit": git_commit(),
        "source_snapshot_date": pinned_date,
        "source_table": "raw/nflverse/player_stats (nflreadpy load_player_stats)",
        "source_schema_version": schema_version("nflverse_player_stats"),
        "scoring_source": "access.metrics.league_scoring_by_column() -- ESPN live settings, charter.md §5",
        "games_threshold": GAMES_THRESHOLD,
        "positions": list(CHARTER_POSITIONS),
        "season_type_included": "REG only",
        "row_count": len(finishes),
        "file": {"path": str(out_path), "sha256": sha256_file(out_path)},
        "note": (
            "Ground truth built from a completed season -- no as-of-date leakage risk by "
            "construction. Kept outside data/snapshots/ and outside frozen_worlds/ per the "
            "phase2 leakage checklist ('ground truth stored outside the world directory with "
            "access denied to candidate runs')."
        ),
    }
    manifest_path = season_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[ground_truth] wrote {out_path} ({len(finishes)} players) and {manifest_path}")
    return out_path


def run(seasons: list[int]) -> int:
    for season in seasons:
        write_ground_truth(season)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--season", type=int, action="append", dest="seasons",
        help="season to build (repeatable). Defaults to both charter frozen-world seasons (2024, 2025).",
    )
    args = parser.parse_args()
    seasons = args.seasons or list(NFLVERSE_STATS_SEASONS)
    return run(seasons)


if __name__ == "__main__":
    sys.exit(main())
