"""Derived-metric computations backing docs/data-dictionary.md
(phase1-data-platform-design.md §5) -- "no metric is ever defined inside a
prompt or notebook" means every formula lives here ONCE, not re-derived by
whichever agent happens to need it.

league-accurate PPG is the flagship requirement ("test it against a
hand-computed example"): nflverse's own `fantasy_points`/`fantasy_points_ppr`
columns use nflverse's own scoring assumptions -- NOT necessarily this
league's (charter §5: 4pt passing TD, 1.0 PPR, no yardage/performance
bonuses). This module computes fantasy points from raw counting stats using
the ACTUAL scoring rules pulled live from ESPN (get_league_scoring()), so
it's correct by construction rather than by coincidence -- verified
2026-07-18 against two hand-computed examples (Josh Allen and Jahmyr Gibbs,
real 2025 week-1 stat lines), both exact matches.

ESPN statId -> player_stats column mapping verified 2026-07-18 against
github.com/cwendt94/espn-api's PLAYER_STATS_MAP (community-maintained,
since ESPN's API is unofficial/undocumented) and cross-checked against
this league's own live scoring settings (statId 4 = 4.0 pts matches
charter's passing-TD rule; statId 53 = 1.0 pt matches the PPR rule).
Scoped to offensive skill-position stats only (QB/RB/WR/TE) -- K/DST
statIds exist in ESPN's scoring table but are out of charter scope (§4)
and not mapped here.
"""

from __future__ import annotations

import pandas as pd

from access.layer import get_league_scoring

# ESPN statId -> player_stats column. Only offensive skill-position stats
# that matter for QB/RB/WR/TE scoring (charter §4 excludes K/DST/IDP).
ESPN_STAT_ID_TO_COLUMN = {
    3: "passing_yards",
    4: "passing_tds",
    19: "passing_2pt_conversions",
    20: "passing_interceptions",
    24: "rushing_yards",
    25: "rushing_tds",
    26: "rushing_2pt_conversions",
    42: "receiving_yards",
    43: "receiving_tds",
    44: "receiving_2pt_conversions",
    53: "receptions",
    63: "fumble_recovery_tds",
}
# Lost fumbles (statId 72) can come from any of three columns in
# player_stats (rushing/receiving/sack-related) -- summed as one combined
# input rather than mapped 1:1, since ESPN scores "lost fumbles" as a
# single category regardless of which play type caused it.
_LOST_FUMBLE_COLUMNS = ["rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost"]
_LOST_FUMBLE_STAT_ID = 72


def league_scoring_by_column() -> dict:
    """{player_stats column name: points per unit}, derived from ESPN's live
    scoring settings -- not hardcoded, so a charter/league-settings change
    is reflected automatically the next time get_league_scoring() is re-run.
    Returns None if get_league_scoring() itself is unavailable."""
    scoring = get_league_scoring()
    if not scoring["available"]:
        return None
    items = scoring["values"]["scoring_settings"]["scoringItems"]
    by_stat_id = {item["statId"]: item["points"] for item in items}

    by_column = {}
    for stat_id, column in ESPN_STAT_ID_TO_COLUMN.items():
        if stat_id in by_stat_id:
            by_column[column] = by_stat_id[stat_id]
    if _LOST_FUMBLE_STAT_ID in by_stat_id:
        by_column["_lost_fumbles"] = by_stat_id[_LOST_FUMBLE_STAT_ID]
    return by_column


def compute_fantasy_points(stat_row, scoring_by_column: dict) -> float:
    """stat_row: a dict-like (pandas Series or dict) of counting stats for
    one player over some window (one game, or summed across several).
    Unmapped/missing columns are treated as 0, not skipped silently --
    every column in scoring_by_column is expected to exist in player_stats.
    """
    total = 0.0
    for column, points in scoring_by_column.items():
        if column == "_lost_fumbles":
            value = sum(float(stat_row.get(c, 0) or 0) for c in _LOST_FUMBLE_COLUMNS)
        else:
            value = float(stat_row.get(column, 0) or 0)
        total += value * points
    return round(total, 2)


def compute_adot(window_rows: pd.DataFrame) -> float | None:
    """Average depth of target = total receiving air yards / total targets."""
    total_targets = window_rows["targets"].sum()
    if total_targets == 0:
        return None
    return round(float(window_rows["receiving_air_yards"].sum() / total_targets), 2)


def compute_epa_per_target(window_rows: pd.DataFrame) -> float | None:
    total_targets = window_rows["targets"].sum()
    if total_targets == 0:
        return None
    return round(float(window_rows["receiving_epa"].sum() / total_targets), 4)


def compute_td_rate(window_rows: pd.DataFrame) -> float | None:
    """(rushing + receiving TDs) / (carries + targets). LOW stability by
    design (phase1 §5 explicitly flags TD_rate as regression-mandatory --
    touchdown rate on a small sample is mostly noise, not skill)."""
    total_touches = window_rows["carries"].sum() + window_rows["targets"].sum()
    if total_touches == 0:
        return None
    total_tds = window_rows["rushing_tds"].sum() + window_rows["receiving_tds"].sum()
    return round(float(total_tds / total_touches), 4)


def compute_carry_share(window_rows: pd.DataFrame, team_stats_df: pd.DataFrame) -> float | None:
    """Player carries / team carries, matched game-by-game (same team,
    season, week) so a mid-season team change doesn't pollute the ratio
    with a different team's total plays."""
    merged = window_rows.merge(
        team_stats_df[["team", "season", "week", "carries"]],
        on=["team", "season", "week"],
        suffixes=("_player", "_team"),
    )
    total_team_carries = merged["carries_team"].sum()
    if total_team_carries == 0:
        return None
    return round(float(merged["carries_player"].sum() / total_team_carries), 4)


# D-017 (agents/prompts.py's opportunity_analyst methodology named this data
# gap explicitly: "weighted opportunity ... red-zone and end-zone usage
# weighted up" / "designed-run vs scramble split... not retrievable").
# Sourced from capture/pull_pbp.py's redzone_player_stats/redzone_team_stats
# (curated-only, D-017 -- full pbp is never persisted). UNLIKE snap_share,
# a missing rz_targets/rz_carries/designed_carries/scramble_carries value
# after the curated-layer left join genuinely MEANS zero (no red-zone/rush
# involvement that game, the overwhelmingly common case) -- fillna(0) is
# correct here, not a data-availability workaround.
_RZ_COUNT_COLUMNS = ["rz_targets", "rz_pass_tds", "rz_carries", "rz_rush_tds", "designed_carries", "scramble_carries"]


def compute_red_zone_target_share(window_rows: pd.DataFrame, team_rz_stats_df: pd.DataFrame) -> float | None:
    """Player red-zone targets / team red-zone pass attempts, matched
    game-by-game (same team, season, week) -- the receiver-side counterpart
    to compute_carry_share, using red-zone (yardline_100 <= 20) volume only."""
    rows = window_rows.copy()
    rows["rz_targets"] = rows["rz_targets"].fillna(0)
    merged = rows.merge(
        team_rz_stats_df[["team", "season", "week", "team_rz_pass_attempts"]],
        on=["team", "season", "week"],
    )
    total_team_rz_attempts = merged["team_rz_pass_attempts"].sum()
    if total_team_rz_attempts == 0:
        return None
    return round(float(merged["rz_targets"].sum() / total_team_rz_attempts), 4)


def compute_red_zone_carry_share(window_rows: pd.DataFrame, team_rz_stats_df: pd.DataFrame) -> float | None:
    """Player red-zone carries / team red-zone rush attempts, matched
    game-by-game (same team, season, week)."""
    rows = window_rows.copy()
    rows["rz_carries"] = rows["rz_carries"].fillna(0)
    merged = rows.merge(
        team_rz_stats_df[["team", "season", "week", "team_rz_rush_attempts"]],
        on=["team", "season", "week"],
    )
    total_team_rz_attempts = merged["team_rz_rush_attempts"].sum()
    if total_team_rz_attempts == 0:
        return None
    return round(float(merged["rz_carries"].sum() / total_team_rz_attempts), 4)


def compute_designed_run_rate(window_rows: pd.DataFrame) -> float | None:
    """designed_carries / (designed_carries + scramble_carries) -- QB-
    specific by construction (scramble_carries is 0 for every non-QB by
    definition, so this returns ~1.0 for any rushing RB/WR, which is
    correct but not the metric's intended use; agents should gate on
    position before reading it, same as any other position-scoped metric
    here). None when the player had zero qualifying rush attempts (kneels
    excluded at the source), not 0 -- "100% scramble" and "never ran" must
    not collapse to the same reported value."""
    designed = window_rows["designed_carries"].fillna(0).sum()
    scramble = window_rows["scramble_carries"].fillna(0).sum()
    total = designed + scramble
    if total == 0:
        return None
    return round(float(designed / total), 4)


# draft_capital_tier: round boundaries are a build-time judgment call, not a
# standard published tiering -- documented here so the boundaries are a
# decision-log-worthy choice, not an implicit one.
_DRAFT_CAPITAL_TIERS = [
    (1, 2, "Premium"),
    (3, 4, "Mid"),
    (5, 7, "Late"),
]


def draft_capital_tier(draft_round) -> str:
    """draft_round: from nflverse_crosswalk's draft_round column (float,
    NaN for undrafted). Returns "Undrafted" for NaN/None -- undrafted
    free agents are a real, meaningful category, not missing data."""
    if draft_round is None or pd.isna(draft_round):
        return "Undrafted"
    draft_round = int(draft_round)
    for lo, hi, label in _DRAFT_CAPITAL_TIERS:
        if lo <= draft_round <= hi:
            return label
    return "Late"  # rounds 8+ (only possible in very old draft classes; modern drafts cap at 7)
