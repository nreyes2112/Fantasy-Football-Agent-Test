"""Scoring engine (docs/phase2-backtest-harness-design.md §3, build-order
step 3) -- implements Appendix M's SM2 (Spearman rank correlation vs.
baselines) plus the MAE diagnostic, against a frozen world's ground truth
(backtest/ground_truth.py).

SCOPE NOTE (read before trusting spearman_overall): Appendix M defines SM2 as
correlation vs. "actual end-of-season value rank ... overall AND in >= 3 of 4
positions." Per-position correlation is unambiguous and fully implemented
here (fixed charter universes, actual PPG-based positional rank). "Overall"
is NOT well-defined yet: pooling raw PPG across positions trivially favors
whichever position scores higher under this league's raw settings (QBs, with
4pt passing TDs) regardless of any board's actual skill at drafting -- Phase
6's VALUE = projected points - positional replacement baseline (VORP) is the
design's actual answer to make cross-position comparison fair, and Phase 6
hasn't been built yet. `spearman_overall_naive` here is a stand-in: overall
ADP rank vs. actual overall raw-PPG rank, pooled across all four positions'
fixed universes -- reported for visibility, explicitly NOT the operational
SM2 "overall" figure until VORP exists to replace it. Don't use it alone to
judge O3's gate.
"""

from __future__ import annotations

import pandas as pd

# charter.md §3: fixed player universes, anti-cherry-picking.
SM2_UNIVERSE_SIZES = {"QB": 24, "RB": 48, "WR": 60, "TE": 24}


def _actual_positional_rank(ground_truth: pd.DataFrame, position: str, universe_gsis: list[str]) -> pd.Series:
    """Actual positional_rank per gsis_id in the universe. A universe player
    with NO ground-truth row (didn't meet charter.md §3's games threshold --
    injury, benching, etc.) is NOT dropped from the correlation, which would
    be survivorship bias in reverse (hiding exactly the downside a rank
    correlation should capture, per phase2 design's anti-pattern checklist).
    Instead they're assigned the worst rank in the universe + 1."""
    pos_truth = ground_truth[ground_truth["position"] == position].set_index("gsis_id")["positional_rank"]
    worst_rank = len(universe_gsis) + 1
    return pd.Series({gid: pos_truth.get(gid, worst_rank) for gid in universe_gsis})


def score_candidate(
    candidate: pd.DataFrame, ground_truth: pd.DataFrame, universe_sizes: dict = SM2_UNIVERSE_SIZES
) -> dict:
    """candidate: DataFrame with columns [gsis_id, position, positional_rank,
    overall_rank] (rank 1 = best/first off the board). Universe per position
    = the candidate's own top-N by positional_rank (charter.md §3 sizes),
    matching Appendix M's "top-N per position by consensus at freeze" with
    the candidate's own rank standing in for consensus (this IS consensus
    when the candidate is the ADP-order baseline itself).
    """
    by_position = {}
    mae_by_position = {}
    overall_rows = []

    for position, universe_size in universe_sizes.items():
        pos_candidate = candidate[candidate["position"] == position].sort_values("positional_rank").head(universe_size)
        universe_gsis = pos_candidate["gsis_id"].tolist()
        if len(universe_gsis) < 2:
            by_position[position] = None
            mae_by_position[position] = None
            continue

        actual = _actual_positional_rank(ground_truth, position, universe_gsis)
        cand_ranks = pos_candidate.set_index("gsis_id")["positional_rank"]
        actual_aligned = actual.reindex(cand_ranks.index)

        # Both series are already rank-valued (1..N), so a plain Pearson
        # correlation on them IS Spearman's rho by definition -- avoids an
        # otherwise-unneeded scipy dependency (pandas' method="spearman"
        # imports scipy internally; scipy isn't installed in either venv,
        # see requirements.txt/requirements-crosswalk.txt).
        corr = cand_ranks.corr(actual_aligned, method="pearson")
        by_position[position] = {
            "spearman": None if pd.isna(corr) else round(float(corr), 4),
            "universe_size": len(universe_gsis),
        }
        mae_by_position[position] = round(float((cand_ranks - actual_aligned).abs().mean()), 2)

        overall_rows.append(
            pd.DataFrame(
                {
                    "gsis_id": pos_candidate["gsis_id"].values,
                    "overall_rank": pos_candidate["overall_rank"].values,
                    "position": position,
                }
            )
        )

    # Naive overall (see module docstring's SCOPE NOTE) -- actual overall
    # rank = raw PPG rank across the pooled universe (all 4 positions
    # together), NOT VORP-adjusted.
    naive_overall = None
    if overall_rows:
        pooled = pd.concat(overall_rows, ignore_index=True)
        gt_indexed = ground_truth.set_index("gsis_id")
        pooled["actual_ppg"] = pooled["gsis_id"].map(gt_indexed["ppg"])
        worst_ppg = pooled["actual_ppg"].min(skipna=True)
        fallback_ppg = (worst_ppg - 0.01) if pd.notna(worst_ppg) else 0.0
        pooled["actual_ppg"] = pooled["actual_ppg"].fillna(fallback_ppg)  # bust/missed-threshold players rank last, not dropped
        pooled["actual_overall_rank"] = pooled["actual_ppg"].rank(method="min", ascending=False)
        # pre-ranked inputs -> Pearson == Spearman's rho, see note above.
        corr = pooled["overall_rank"].corr(pooled["actual_overall_rank"], method="pearson")
        naive_overall = None if pd.isna(corr) else round(float(corr), 4)

    return {
        "spearman_by_pos": by_position,
        "spearman_overall_naive": naive_overall,
        "mae_by_pos": mae_by_position,
        "positions_beating_naive_zero": sum(
            1 for v in by_position.values() if v and v["spearman"] is not None and v["spearman"] > 0
        ),
    }
