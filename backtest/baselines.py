"""Baseline Bank (docs/phase2-backtest-harness-design.md §4, build-order step
4) -- "the market" the Charter requires beating (O3).

ADP-order and ECR: both worlds. Naive-repeat ("rank = last season's PPG
finish"): 2025-06-18 world only -- its prior season is 2024, already in
backtest/ground_truth/. The 2024-06-18 world's prior season is 2023, never
pulled (Nick deprioritized going further into the past, 2026-07-18), so
naive-repeat isn't available there; NOT silently skipped -- run() reports it
missing rather than pretending both worlds got scored.

Usage (either venv):
    python -m backtest.baselines               # both worlds, all available baselines
    python -m backtest.baselines --world 2024-06-18
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.frozen_worlds.historical_adp import WAYBACK_SNAPSHOTS
from backtest.scoring import SM2_UNIVERSE_SIZES, score_candidate
from capture.manifest_utils import git_commit

FROZEN_WORLDS_ROOT = Path("backtest/frozen_worlds")
GROUND_TRUTH_ROOT = Path("backtest/ground_truth")
SCORECARDS_ROOT = Path("backtest/scorecards")

# world_date's year IS the outcome season -- the frozen world sits mid-year,
# before that same year's season is played.
WORLD_TO_SEASON = {"2024-06-18": 2024, "2025-06-18": 2025}


def load_adp_candidate(world_date: str) -> pd.DataFrame:
    path = FROZEN_WORLDS_ROOT / world_date / "raw" / "fantasypros_adp" / "adp.parquet"
    df = pd.read_parquet(path)
    df = df[df["gsis_id"].notna()].copy()
    return df.rename(columns={"rank": "overall_rank", "position_rank_at_snapshot": "positional_rank"})[
        ["gsis_id", "position", "positional_rank", "overall_rank"]
    ]


def load_ecr_candidate(world_date: str) -> pd.DataFrame:
    path = FROZEN_WORLDS_ROOT / world_date / "raw" / "fantasypros_ecr" / "ecr.parquet"
    df = pd.read_parquet(path)
    df = df[df["gsis_id"].notna()].copy()
    return df.rename(columns={"overall_rank_ecr": "overall_rank"})[["gsis_id", "position", "positional_rank", "overall_rank"]]


def load_ground_truth(season: int) -> pd.DataFrame:
    return pd.read_parquet(GROUND_TRUTH_ROOT / str(season) / "positional_finishes.parquet")


def ground_truth_available(season: int) -> bool:
    return (GROUND_TRUTH_ROOT / str(season) / "positional_finishes.parquet").exists()


def load_naive_repeat_candidate(prior_season: int) -> pd.DataFrame:
    """Candidate = prior season's actual finish, used as this year's guess
    ('rank = last season's PPG finish', phase2 §4). positional_rank comes
    straight from the prior season's ground truth; overall_rank is that same
    season's raw-PPG rank pooled across ALL positions (not just the charter
    universe) -- consistent with how scoring.py's naive_overall figure is
    computed, so naive-repeat's own "overall" isn't held to a different
    standard than the one used to judge it."""
    prior_gt = load_ground_truth(prior_season)
    candidate = prior_gt[["gsis_id", "position", "positional_rank"]].copy()
    candidate["overall_rank"] = prior_gt["ppg"].rank(method="min", ascending=False)
    return candidate


def load_uniform_blend_candidate(world_date: str) -> tuple[pd.DataFrame, list[str]]:
    """phase2 §4: "Uniform-blend | Simple average of the above". Averages
    each player's positional_rank (and overall_rank) across whichever
    baselines actually exist for this world, then re-ranks the averages into
    clean 1..N integer ranks -- NOT a straight concat/rescore, since each
    source's raw ranks already span its own full published list (not
    pre-truncated to the charter universe), so averaging first and letting
    score_candidate's own top-N selection run on the blended rank is the
    correct order of operations. Naive-repeat is included only where its
    prior season's ground truth exists (2025-06-18 only, as of 2026-07-19) --
    "the above" naturally means whatever baselines this project has actually
    built, not a fixed list independent of data availability.
    """
    season = WORLD_TO_SEASON[world_date]
    sources = {"adp_order": load_adp_candidate(world_date), "ecr": load_ecr_candidate(world_date)}
    prior_season = season - 1
    if ground_truth_available(prior_season):
        sources["naive_repeat"] = load_naive_repeat_candidate(prior_season)

    tagged = [df.assign(_source=name) for name, df in sources.items()]
    combined = pd.concat(tagged, ignore_index=True)

    blended = combined.groupby(["gsis_id", "position"], as_index=False).agg(
        positional_rank=("positional_rank", "mean"), overall_rank=("overall_rank", "mean")
    )
    # Re-rank the averaged (fractional) ranks into clean integers per position
    # and overall, so this candidate looks like any other to score_candidate.
    blended["positional_rank"] = blended.groupby("position")["positional_rank"].rank(method="min").astype(int)
    blended["overall_rank"] = blended["overall_rank"].rank(method="min").astype(int)
    return blended, list(sources.keys())


def config_hash(world_date: str, system: str) -> str:
    payload = json.dumps({"world": world_date, "system": system, "universe_sizes": SM2_UNIVERSE_SIZES}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _write_scorecard(run_id: str, world_date: str, season: int, system: str, result: dict, extra_notes: str = "") -> Path:
    scorecard = {
        "run_id": run_id,
        "config_hash": config_hash(world_date, system),
        "world": world_date,
        "outcome_season": season,
        "system": system,
        "runs": 1,  # deterministic (no LLM involved) -- the 3+-run variance protocol (phase2 §5) doesn't apply
        "generated_at": datetime.now().isoformat(),
        "code_git_commit": git_commit(),
        "headline": {
            "spearman_by_pos": result["spearman_by_pos"],
            "spearman_overall_naive": result["spearman_overall_naive"],
        },
        "diagnostics": {
            "mae_by_pos": result["mae_by_pos"],
            "positions_beating_naive_zero": result["positions_beating_naive_zero"],
        },
        "notes": (
            "spearman_overall_naive is NOT VORP-adjusted (see backtest/scoring.py's module docstring) -- "
            "raw-PPG pooling across positions trivially favors QBs under this league's 4pt-passing-TD scoring, "
            "so it is not yet the operational SM2 'overall' figure. Per-position spearman_by_pos is the "
            "trustworthy signal from this run. tier_accuracy (SM3) not computed -- needs Phase 6's Gaussian-"
            "mixture tiering, not built yet." + (" " + extra_notes if extra_notes else "")
        ),
    }
    SCORECARDS_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = SCORECARDS_ROOT / f"{run_id}.json"
    out_path.write_text(json.dumps(scorecard, indent=2))
    print(f"[baselines] {world_date}/{system}: wrote {out_path}")
    print(f"  spearman_by_pos: {result['spearman_by_pos']}")
    print(f"  spearman_overall_naive: {result['spearman_overall_naive']}")
    return out_path


def run_adp_order_baseline(world_date: str) -> Path:
    season = WORLD_TO_SEASON[world_date]
    candidate = load_adp_candidate(world_date)
    ground_truth = load_ground_truth(season)
    result = score_candidate(candidate, ground_truth)
    return _write_scorecard(f"baseline_adp_order__{world_date}", world_date, season, "baseline_adp_order", result)


def run_ecr_baseline(world_date: str) -> Path:
    season = WORLD_TO_SEASON[world_date]
    candidate = load_ecr_candidate(world_date)
    ground_truth = load_ground_truth(season)
    result = score_candidate(candidate, ground_truth)
    return _write_scorecard(f"baseline_ecr__{world_date}", world_date, season, "baseline_ecr", result)


def run_naive_repeat_baseline(world_date: str) -> Path | None:
    season = WORLD_TO_SEASON[world_date]
    prior_season = season - 1
    if not ground_truth_available(prior_season):
        print(
            f"[baselines] {world_date}/baseline_naive_repeat: SKIPPED -- prior season {prior_season}'s ground "
            "truth doesn't exist (not pulled, deprioritized per Nick 2026-07-18). NOT scored, not faked."
        )
        return None
    candidate = load_naive_repeat_candidate(prior_season)
    ground_truth = load_ground_truth(season)
    result = score_candidate(candidate, ground_truth)
    return _write_scorecard(
        f"baseline_naive_repeat__{world_date}", world_date, season, "baseline_naive_repeat", result,
        extra_notes=f"Candidate = actual {prior_season} season finish used as the {season} guess.",
    )


def run_uniform_blend_baseline(world_date: str) -> Path:
    season = WORLD_TO_SEASON[world_date]
    candidate, sources_used = load_uniform_blend_candidate(world_date)
    ground_truth = load_ground_truth(season)
    result = score_candidate(candidate, ground_truth)
    return _write_scorecard(
        f"baseline_uniform_blend__{world_date}", world_date, season, "baseline_uniform_blend", result,
        extra_notes=f"Blended from: {sources_used}.",
    )


def run(world_dates: list[str]) -> int:
    for world_date in world_dates:
        run_adp_order_baseline(world_date)
        run_ecr_baseline(world_date)
        run_naive_repeat_baseline(world_date)
        run_uniform_blend_baseline(world_date)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", action="append", dest="world_dates", choices=list(WAYBACK_SNAPSHOTS))
    args = parser.parse_args()
    return run(args.world_dates or list(WAYBACK_SNAPSHOTS))


if __name__ == "__main__":
    sys.exit(main())
