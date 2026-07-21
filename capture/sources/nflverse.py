"""nflverse player-ID crosswalk (phase1-data-platform-design.md §3).

Canonical key is nflverse gsis_id. `load_ff_playerids()` is nflreadpy's port
of DynastyProcess.com's community-maintained crosswalk, mapping gsis_id
against sleeper_id, espn_id, fantasypros_id, mfl_id, and others in one table
-- this is what lets Sleeper and ESPN's raw player tables resolve to a single
canonical ID without name matching.

Requires Python >= 3.10 (nflreadpy's own constraint) -- run under .venv311,
not the project's original .venv (3.9), which is why this module is isolated
from capture/sources/{sleeper,ffc_adp,espn}.py's runtime.
"""

from __future__ import annotations

import nflreadpy as nfl
import pandas as pd

# nflverse uses "LA" for the Rams (a legacy convention) in player_stats AND
# team_stats (verified 2026-07-18); every other source in this project
# (Sleeper, ESPN) uses "LAR". Normalized here at the source, on every table
# that carries a `team` column, so nothing downstream needs to special-case
# it or silently fail to match Sleeper's current-team field.
_TEAM_CODE_ALIASES = {"LA": "LAR"}


def fetch_ff_playerids() -> pd.DataFrame:
    """One row per player across platforms. Includes gsis_id, sleeper_id,
    espn_id, fantasypros_id, mfl_id, name, position, team, and more --
    see data/schemas/nflverse_ff_playerids/v1.json for the columns this
    project actually keeps."""
    df = nfl.load_ff_playerids()
    return df.to_pandas()


def fetch_player_stats(seasons: list[int]) -> pd.DataFrame:
    """Weekly player stats (145 columns: passing/rushing/receiving counting
    stats, EPA, target_share, air_yards_share, wopr, racr, etc.) for the
    given seasons. `player_id` here is ALREADY the canonical gsis_id --
    verified 2026-07-18 (e.g. "00-0023459") -- so this table needs no
    crosswalk join, unlike Sleeper/ESPN/FFC.

    Note: nflverse's own `fantasy_points`/`fantasy_points_ppr` columns use
    nflverse's default scoring assumptions, which are NOT verified to match
    this league's exact settings (charter §5: 4pt passing TD, 1.0 PPR) --
    don't treat them as this league's PPG without checking get_league_scoring()
    first. Compute league-accurate fantasy points from the raw counting
    stats instead.
    """
    df = nfl.load_player_stats(seasons=seasons, summary_level="week").to_pandas()
    df["team"] = df["team"].replace(_TEAM_CODE_ALIASES)
    return df


def fetch_team_stats(seasons: list[int]) -> pd.DataFrame:
    """Weekly team-level stats (133 columns -- team-side passing/rushing/
    receiving/defense counting stats, EPA). Used for get_team_context's
    plays-per-game and pass-rate; does NOT include Vegas season win totals
    (a separate betting-market product nflverse doesn't carry, and no free
    source for it has been found -- see D-006's zero-spend constraint) or
    O-line rank (not a raw stat; would need a paid analyst ranking like PFF).
    Both are reported as genuinely unavailable by get_team_context rather
    than guessed.
    """
    df = nfl.load_team_stats(seasons=seasons, summary_level="week").to_pandas()
    df["team"] = df["team"].replace(_TEAM_CODE_ALIASES)
    return df


def fetch_redzone_pbp_summary(seasons: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Red-zone (yardline_100 <= 20) target/carry volume and QB designed-run
    vs. scramble split, aggregated from play-by-play down to two small
    per-game tables (player-level, team-level) -- built for Agent 1's
    (opportunity/volume) own stated evidence hierarchy: "weighted opportunity
    ... with red-zone and end-zone usage weighted up" and its own
    data_gaps entry "designed-run vs scramble split and red-zone rush share
    not retrievable" (D-015/D-016).

    DEVIATION from pull_stats.py's "raw is never filtered" precedent
    (D-017): full play-by-play is 372 columns / ~20.7MB per season (measured
    2026-07-19) vs. ~0.2MB for the 15 columns this aggregation actually
    needs -- persisting two full seasons of raw pbp (~40MB) into this
    project's git-tracked data/snapshots/ tree for a handful of derived
    counts was judged not worth the repo bloat. Only the aggregated output
    is written; the full pbp table is fetched into memory, aggregated, and
    discarded, never touching disk. Verified 2026-07-19 that rusher_player_id/
    receiver_player_id/passer_player_id are ALREADY gsis_id format
    (e.g. "00-0034844"), same as player_stats -- no crosswalk join needed.

    qb_kneel plays are EXCLUDED from both designed_carries and
    scramble_carries (neither reflects real rushing opportunity -- a kneel
    is clock management, not a play call or an evasive scramble).
    """
    df = nfl.load_pbp(seasons=seasons).to_pandas()
    df = df[df["season_type"] == "REG"].copy()  # matches ground_truth.py / charter §3's REG-only policy
    df["team"] = df["posteam"].replace(_TEAM_CODE_ALIASES)
    rz = df[df["yardline_100"] <= 20]

    key = ["season", "week", "season_type", "team"]

    receiver_agg = (
        rz[rz["pass_attempt"] == 1]
        .groupby(key + ["receiver_player_id"], as_index=False)
        .agg(rz_targets=("pass_attempt", "size"), rz_pass_tds=("pass_touchdown", "sum"))
        .rename(columns={"receiver_player_id": "player_id"})
    )

    rusher_rows = rz[rz["rush_attempt"] == 1]
    rusher_agg = (
        rusher_rows.groupby(key + ["rusher_player_id"], as_index=False)
        .agg(rz_carries=("rush_attempt", "size"), rz_rush_tds=("rush_touchdown", "sum"))
        .rename(columns={"rusher_player_id": "player_id"})
    )

    # designed_carries/scramble_carries use ALL rush attempts (not just
    # red-zone ones) -- a QB's season-long run-call profile, not a red-zone-
    # only slice, is what the methodology's "designed-run vs scramble split"
    # data gap actually asks for.
    all_rush = df[(df["rush_attempt"] == 1) & (df["qb_kneel"] != 1)]
    designed_scramble_agg = (
        all_rush.groupby(key + ["rusher_player_id"], as_index=False)
        .agg(
            designed_carries=("qb_scramble", lambda s: int((s != 1).sum())),
            scramble_carries=("qb_scramble", lambda s: int((s == 1).sum())),
        )
        .rename(columns={"rusher_player_id": "player_id"})
    )

    player_df = receiver_agg.merge(rusher_agg, on=key + ["player_id"], how="outer").merge(
        designed_scramble_agg, on=key + ["player_id"], how="outer"
    )
    for col in ("rz_targets", "rz_pass_tds", "rz_carries", "rz_rush_tds", "designed_carries", "scramble_carries"):
        player_df[col] = player_df[col].fillna(0).astype(int)
    player_df = player_df.dropna(subset=["player_id"])

    team_agg = (
        rz.groupby(key, as_index=False).agg(
            team_rz_pass_attempts=("pass_attempt", "sum"), team_rz_rush_attempts=("rush_attempt", "sum")
        )
    )
    team_agg["team_rz_pass_attempts"] = team_agg["team_rz_pass_attempts"].astype(int)
    team_agg["team_rz_rush_attempts"] = team_agg["team_rz_rush_attempts"].astype(int)

    return player_df, team_agg


def fetch_snap_counts(seasons: list[int]) -> pd.DataFrame:
    """Weekly snap counts (offense/defense/special-teams snaps and shares),
    sourced from Pro Football Reference via nflreadpy. Unlike player_stats/
    team_stats, this table is keyed by `pfr_player_id` (e.g. "BankKe01"),
    NOT gsis_id -- verified 2026-07-18 that nflverse_crosswalk's own `pfr_id`
    column uses the exact same format, so resolution reuses
    capture.crosswalk.resolve_source() the same way Sleeper/ESPN do, rather
    than needing a new matching mechanism. `offense_pct` is already exactly
    phase1 §5's `snap_share` metric -- PFR/nflverse compute it directly, no
    derivation needed once resolved to gsis_id.
    """
    df = nfl.load_snap_counts(seasons=seasons).to_pandas()
    df["team"] = df["team"].replace(_TEAM_CODE_ALIASES)
    return df
