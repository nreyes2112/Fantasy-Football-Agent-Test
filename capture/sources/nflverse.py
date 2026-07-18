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
