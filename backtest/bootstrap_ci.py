"""Bootstrap confidence intervals (docs/phase2-backtest-harness-design.md §5,
Phase 2 exit checklist's last item) -- "Bootstrap confidence intervals on
Spearman and hit-rate deltas vs. baselines (resample the player universe,
~2,000 iterations). Report the CI, not just the point delta."

Scope: Spearman CIs only. Hit-rate CIs need the Top-N hit-rate diagnostic
metric (phase2 §3), which isn't computed anywhere yet -- not faked here;
`compute_baseline_delta_cis()`'s docstring flags this explicitly. Spearman is
the metric this project actually has real numbers for (the Baseline Bank,
D-010/D-011/D-012), so that's what gets a real, verified CI implementation.

"Verified against a hand-checked example" (exit checklist's own words): a
perfectly-correlated synthetic dataset has a mathematically exact bootstrap
answer, not just an eyeballed one -- resampling (x_i, y_i) pairs where
x_i == y_i for every i preserves that equality in every resample, so the
Spearman correlation is EXACTLY 1.0 (not approximately) in every single
bootstrap iteration, and the resulting CI must collapse to the single point
[1.0, 1.0]. Same logic gives an exact -1.0 for a perfectly reversed series.
`verify_against_hand_checked_example()` asserts this precisely rather than
just printing something plausible-looking.

Usage (either venv):
    python -m backtest.bootstrap_ci
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import baselines

SCORECARDS_ROOT = Path("backtest/scorecards")
DEFAULT_N_ITERATIONS = 2000
DEFAULT_CI = 0.95


def _percentile_ci(values: list[float], ci: float) -> dict:
    clean = np.array([v for v in values if v is not None and not np.isnan(v)])
    if len(clean) == 0:
        return {"point_estimate": None, "ci_low": None, "ci_high": None, "n_valid": 0}
    lo_pct = (1 - ci) / 2 * 100
    hi_pct = (1 + ci) / 2 * 100
    return {
        "point_estimate": round(float(np.mean(clean)), 4),
        "ci_low": round(float(np.percentile(clean, lo_pct)), 4),
        "ci_high": round(float(np.percentile(clean, hi_pct)), 4),
        "n_valid": int(len(clean)),
    }


def bootstrap_spearman_delta(
    candidate_ranks: pd.Series,
    baseline_ranks: pd.Series,
    actual_ranks: pd.Series,
    n_iterations: int = DEFAULT_N_ITERATIONS,
    ci: float = DEFAULT_CI,
    seed: int | None = None,
) -> dict:
    """All three series must share the same index (e.g. gsis_id) -- each
    bootstrap iteration resamples that index WITH REPLACEMENT and recomputes
    both systems' Spearman correlation against the same resampled actual
    ranks, so the delta is computed on matched resamples (not independently
    resampled series, which would overstate the delta's true variance).

    Ranks are assumed pre-ranked integers (1..N) as everywhere else in this
    project (backtest/scoring.py) -- Pearson correlation on pre-ranked data
    IS Spearman's rho, no scipy dependency needed.
    """
    rng = np.random.default_rng(seed)
    idx = actual_ranks.index.to_numpy()
    n = len(idx)

    cand_corrs, base_corrs, deltas = [], [], []
    for _ in range(n_iterations):
        sample_idx = rng.choice(idx, size=n, replace=True)
        actual_s = actual_ranks.loc[sample_idx].reset_index(drop=True)
        cand_s = candidate_ranks.loc[sample_idx].reset_index(drop=True)
        base_s = baseline_ranks.loc[sample_idx].reset_index(drop=True)
        cand_corr = cand_s.corr(actual_s, method="pearson")
        base_corr = base_s.corr(actual_s, method="pearson")
        cand_corrs.append(cand_corr)
        base_corrs.append(base_corr)
        # NaN (zero-variance resample -- astronomically rare but possible)
        # propagates naturally; _percentile_ci filters NaN out below.
        deltas.append(cand_corr - base_corr if pd.notna(cand_corr) and pd.notna(base_corr) else np.nan)

    return {
        "n_iterations": n_iterations,
        "confidence_level": ci,
        "n_players": n,
        "candidate_spearman": _percentile_ci(cand_corrs, ci),
        "baseline_spearman": _percentile_ci(base_corrs, ci),
        "delta": _percentile_ci(deltas, ci),
    }


def verify_against_hand_checked_example() -> bool:
    """10 synthetic players. candidate_ranks == actual_ranks exactly (a
    perfect predictor) -> Spearman must be EXACTLY 1.0 in every bootstrap
    iteration, since resampling preserves elementwise equality. baseline_ranks
    is the exact reverse (11 - actual) -> Spearman must be EXACTLY -1.0 in
    every iteration for the same reason. Therefore the CI for both, and for
    their delta (2.0), must collapse to single points -- not approximately,
    exactly, by construction. If this doesn't hold the implementation is
    broken, not the test."""
    idx = pd.RangeIndex(10)
    actual = pd.Series(range(1, 11), index=idx)
    candidate = pd.Series(range(1, 11), index=idx)  # == actual
    baseline = pd.Series(range(10, 0, -1), index=idx)  # == 11 - actual

    result = bootstrap_spearman_delta(candidate, baseline, actual, n_iterations=2000, ci=0.95, seed=42)

    checks = [
        ("candidate point_estimate == 1.0", result["candidate_spearman"]["point_estimate"] == 1.0),
        ("candidate CI collapses to [1.0, 1.0]", result["candidate_spearman"]["ci_low"] == 1.0 and result["candidate_spearman"]["ci_high"] == 1.0),
        ("baseline point_estimate == -1.0", result["baseline_spearman"]["point_estimate"] == -1.0),
        ("baseline CI collapses to [-1.0, -1.0]", result["baseline_spearman"]["ci_low"] == -1.0 and result["baseline_spearman"]["ci_high"] == -1.0),
        ("delta point_estimate == 2.0", result["delta"]["point_estimate"] == 2.0),
        ("delta CI collapses to [2.0, 2.0]", result["delta"]["ci_low"] == 2.0 and result["delta"]["ci_high"] == 2.0),
        ("all 2000 iterations valid, none NaN", result["candidate_spearman"]["n_valid"] == 2000),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise AssertionError(f"bootstrap_ci hand-checked example FAILED: {failed}. Full result: {result}")
    print("[bootstrap_ci] hand-checked example PASSED: perfect (+1.0) and perfectly-reversed (-1.0) synthetic "
          "series both collapse to exact-point CIs across 2000 iterations, delta = exactly 2.0.")
    return True


def run_demo_cis(world_dates: list[str]) -> dict:
    """Real application: bootstrap CI on the Spearman delta between two of
    this project's own baselines (ECR vs ADP-order, both already-scored in
    D-010/D-011) for every charter position, both worlds -- gives the honest
    "how confident are we in that delta" answer the point estimates in
    D-010/D-011 never had. Once Phase 3 exists, a real agent-vs-ADP
    comparison calls bootstrap_spearman_delta() the same way.

    Hit-rate CIs are NOT included (see module docstring) -- the Top-N hit-
    rate diagnostic metric itself isn't computed anywhere in this project
    yet, so there's nothing to bootstrap.
    """
    from backtest.scoring import SM2_UNIVERSE_SIZES

    all_results = {}
    for world_date in world_dates:
        season = baselines.WORLD_TO_SEASON[world_date]
        ground_truth = baselines.load_ground_truth(season).set_index("gsis_id")["positional_rank"]
        ecr = baselines.load_ecr_candidate(world_date)
        adp = baselines.load_adp_candidate(world_date)

        world_results = {}
        for position, universe_size in SM2_UNIVERSE_SIZES.items():
            ecr_pos = ecr[ecr["position"] == position].sort_values("positional_rank").head(universe_size).set_index("gsis_id")
            adp_pos = adp[adp["position"] == position].sort_values("positional_rank").head(universe_size).set_index("gsis_id")
            shared = ecr_pos.index.intersection(adp_pos.index)  # bootstrap needs one shared index across both candidates + actual
            if len(shared) < 5:
                continue
            worst_rank = universe_size + 1
            actual = pd.Series({gid: ground_truth.get(gid, worst_rank) for gid in shared})
            world_results[position] = bootstrap_spearman_delta(
                ecr_pos.loc[shared, "positional_rank"], adp_pos.loc[shared, "positional_rank"], actual, seed=42
            )
        all_results[world_date] = world_results
    return all_results


def main() -> int:
    verify_against_hand_checked_example()  # must pass before trusting anything below

    world_dates = list(baselines.WORLD_TO_SEASON)
    results = run_demo_cis(world_dates)
    out_path = SCORECARDS_ROOT / "bootstrap_ci_ecr_vs_adp.json"
    SCORECARDS_ROOT.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"[bootstrap_ci] wrote real ECR-vs-ADP-order delta CIs (both worlds, all positions) to {out_path}")
    for world_date, positions in results.items():
        for position, r in positions.items():
            d = r["delta"]
            print(f"  {world_date}/{position}: ECR-ADP delta = {d['point_estimate']} (95% CI [{d['ci_low']}, {d['ci_high']}], n={r['n_players']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
