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
    df = nfl.load_player_stats(seasons=seasons, summary_level="week")
    return df.to_pandas()


# nflverse's load_team_stats uses "LA" for the Rams (a legacy convention);
# every other source in this project (Sleeper, ESPN) uses "LAR". Normalized
# here at the source so nothing downstream needs to special-case it.
_TEAM_CODE_ALIASES = {"LA": "LAR"}


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
