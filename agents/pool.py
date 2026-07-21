"""Bounded per-position player pools (docs/phase3-agent-prompts.md
Integration Note 3: "Invoke each agent once per position with {{POSITION}}
and a bounded {{PLAYER_POOL}}").

Pool membership = the market's top N per position (charter.md §3's fixed
universes: QB24/RB48/WR60/TE24, "by consensus at freeze" -- with the world's
own archived ADP standing in for consensus in a frozen world, same stand-in
already used by Phase 1's acceptance metric and Phase 2's scoring). Pool
SELECTION is harness code, not an agent choice (phase4 design: agenda and
structure are code); the agent only ever sees the roster of names, not the
ADP values or their ordering (agents/prompts.py alphabetizes).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.scoring import SM2_UNIVERSE_SIZES

FROZEN_WORLDS_ROOT = Path("backtest/frozen_worlds")


def build_player_pool(world_date: str, position: str) -> pd.DataFrame:
    """Top-N {position} players by the world's archived ADP, N per charter §3.
    Returns [gsis_id, player_name, team, position, pool_rank_source] -- the
    source rank column is kept for harness-side bookkeeping only and MUST NOT
    be rendered into prompts for non-market agents."""
    if position not in SM2_UNIVERSE_SIZES:
        raise ValueError(f"position {position!r} not in charter universes {sorted(SM2_UNIVERSE_SIZES)}")
    adp_path = FROZEN_WORLDS_ROOT / world_date / "raw" / "fantasypros_adp" / "adp.parquet"
    if not adp_path.exists():
        raise FileNotFoundError(f"{adp_path} does not exist -- unknown world {world_date!r}?")
    adp = pd.read_parquet(adp_path)
    adp = adp[(adp["position"] == position) & adp["gsis_id"].notna()].copy()
    n = SM2_UNIVERSE_SIZES[position]
    pool = adp.sort_values("position_rank_at_snapshot").head(n)
    if len(pool) < n:
        raise ValueError(
            f"world {world_date} has only {len(pool)} resolved {position}s, need {n} for the charter universe"
        )
    pool = pool.rename(columns={"position_rank_at_snapshot": "pool_rank_source"})
    # FantasyPros' archived pages use "JAC" for Jacksonville; every other
    # source in this project (nflverse, Ourlads, Sleeper) uses "JAX" --
    # normalized here so downstream tool lookups (team context, depth chart,
    # vacated opportunity) resolve. Same class of quirk as the LA/LAR Rams
    # fix in capture/ (see CLAUDE.md); no other pool team code diverges.
    pool["team"] = pool["team"].replace({"JAC": "JAX"})
    return pool[["gsis_id", "player_name", "team", "position", "pool_rank_source"]].reset_index(drop=True)
