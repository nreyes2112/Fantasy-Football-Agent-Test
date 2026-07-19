"""One-command backtest runner (docs/phase2-backtest-harness-design.md §1,
build-order step 5) -- "run_backtest --system <config> --world <date>
--runs 3". Identical code path for every system: candidates are just
[gsis_id, position, positional_rank, overall_rank] tables scored by
backtest/scoring.py against backtest/ground_truth/, same as the Baseline
Bank (backtest/baselines.py) already does -- this module is the general
front door to that same mechanism, not a separate path.

Config hash (phase2 §1: "{prompt versions, rubric version, agent weights,
metric dictionary version, code commit, world date} -> SHA-256"): baseline
systems have no prompts/rubric/agent weights (Phase 3 doesn't exist yet), so
those fields are None for now -- the hash payload is written to be
extensible, not narrowed to only what baselines need.

Scorecard schema matches phase2 §6 AS FAR AS THIS PROJECT CAN HONESTLY FILL
IT IN today: tier_accuracy/myguys_sim/topN_hits/calibration/bust_avoidance/
positional_failure_flags/vs_baselines/verdict all need infrastructure that
doesn't exist yet (Phase 6 tiering, the My Guys selection rule, bootstrap CI,
a real non-baseline candidate to compare against). Rather than fake them or
silently drop them from the schema, they're present and explicitly null with
a reason -- so the schema is forward-compatible today and a future run
against a real agent fills them in without a schema migration.

3+ runs (phase2 §5): every system here is currently deterministic (no LLM
involved), so running N times and averaging is mechanically real but
produces range=[x,x] -- this is the infrastructure proving itself end-to-end
ahead of Phase 3's actual nondeterministic agents, not a meaningful variance
measurement yet. Documented in each scorecard's notes, not hidden.

Usage (either venv):
    python -m backtest.run_backtest --system baseline_adp_order --world 2024-06-18 --runs 3
    python -m backtest.run_backtest --system baseline_naive_repeat --world 2025-06-18 --runs 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest import baselines
from backtest.scoring import SM2_UNIVERSE_SIZES, score_candidate

SCORECARDS_ROOT = Path("backtest/scorecards")


class SystemUnavailableError(RuntimeError):
    pass


def _adp_order(world_date: str) -> pd.DataFrame:
    return baselines.load_adp_candidate(world_date)


def _ecr(world_date: str) -> pd.DataFrame:
    return baselines.load_ecr_candidate(world_date)


def _naive_repeat(world_date: str) -> pd.DataFrame:
    season = baselines.WORLD_TO_SEASON[world_date]
    prior_season = season - 1
    if not baselines.ground_truth_available(prior_season):
        raise SystemUnavailableError(
            f"baseline_naive_repeat needs {prior_season}'s ground truth, which doesn't exist "
            "(not pulled, deprioritized per Nick 2026-07-18)"
        )
    return baselines.load_naive_repeat_candidate(prior_season)


def _uniform_blend(world_date: str) -> pd.DataFrame:
    candidate, _sources_used = baselines.load_uniform_blend_candidate(world_date)
    return candidate


# Every "system" this project can score today. A future Phase 3 agent config
# registers here the same way -- run_backtest doesn't know or care whether a
# candidate came from a deterministic baseline or an LLM agent chain, only
# that it produces the same [gsis_id, position, positional_rank, overall_rank]
# shape scoring.py already expects.
SYSTEM_REGISTRY = {
    "baseline_adp_order": _adp_order,
    "baseline_ecr": _ecr,
    "baseline_naive_repeat": _naive_repeat,
    "baseline_uniform_blend": _uniform_blend,
}


def compute_config_hash(system: str, world_date: str, config: dict | None = None) -> str:
    config = config or {}
    payload = {
        "system": system,
        "world": world_date,
        "universe_sizes": SM2_UNIVERSE_SIZES,
        # Phase 3 doesn't exist yet -- these stay None for every system
        # registered today, but the hash payload includes the keys now so
        # a future agent config changes the hash without a schema change.
        "prompt_version": config.get("prompt_version"),
        "rubric_version": config.get("rubric_version"),
        "agent_weights": config.get("agent_weights"),
        "metric_dictionary_version": config.get("metric_dictionary_version"),
        "code_git_commit": _git_commit(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _git_commit() -> str | None:
    from capture.manifest_utils import git_commit

    return git_commit()


def _mean_range(values: list[float | None]) -> dict | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return {"mean": round(sum(clean) / len(clean), 4), "range": [round(min(clean), 4), round(max(clean), 4)]}


def run_backtest(system: str, world_date: str, runs: int = 3, config: dict | None = None) -> Path:
    if system not in SYSTEM_REGISTRY:
        raise ValueError(f"unknown system {system!r} -- registered systems: {sorted(SYSTEM_REGISTRY)}")
    if world_date not in baselines.WORLD_TO_SEASON:
        raise ValueError(f"unknown world {world_date!r} -- known worlds: {sorted(baselines.WORLD_TO_SEASON)}")

    season = baselines.WORLD_TO_SEASON[world_date]
    ground_truth = baselines.load_ground_truth(season)

    per_run_results = []
    for _ in range(runs):
        candidate = SYSTEM_REGISTRY[system](world_date)  # raises SystemUnavailableError if genuinely can't run
        per_run_results.append(score_candidate(candidate, ground_truth))

    positions = list(SM2_UNIVERSE_SIZES)
    spearman_by_pos = {
        pos: _mean_range([r["spearman_by_pos"][pos]["spearman"] for r in per_run_results if r["spearman_by_pos"].get(pos)])
        for pos in positions
    }
    spearman_overall_naive = _mean_range([r["spearman_overall_naive"] for r in per_run_results])
    mae_by_pos = {
        pos: _mean_range([r["mae_by_pos"].get(pos) for r in per_run_results]) for pos in positions
    }

    run_id = f"{system}__{world_date}__{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    scorecard = {
        "run_id": run_id,
        "config_hash": compute_config_hash(system, world_date, config),
        "world": world_date,
        "outcome_season": season,
        "system": system,
        "runs": runs,
        "generated_at": datetime.now().isoformat(),
        "code_git_commit": _git_commit(),
        "headline": {
            "spearman_overall": None,  # NOT the same as spearman_overall_naive below -- needs Phase 6 VORP, not built
            "spearman_overall_naive": spearman_overall_naive,
            "spearman_by_pos": spearman_by_pos,
            "tier_accuracy": None,
            "myguys_sim": None,
        },
        "diagnostics": {
            "mae_by_pos": mae_by_pos,
            "topN_hits": None,
            "calibration": None,
            "bust_avoidance": None,
            "positional_failure_flags": [
                pos for pos in positions
                if spearman_by_pos[pos] and spearman_by_pos["WR"] and spearman_by_pos[pos]["mean"] < spearman_by_pos["WR"]["mean"] - 0.15
            ] or None,
        },
        "vs_baselines": None,
        "verdict": None,
        "notes": (
            "headline.spearman_overall / tier_accuracy / myguys_sim, diagnostics.topN_hits / calibration / "
            "bust_avoidance, and vs_baselines / verdict are all null -- they need infrastructure not built yet "
            "(Phase 6 VORP for a fair cross-position overall rank, Phase 6 tiering, the My Guys selection rule, "
            "bootstrap CIs, and/or a real non-baseline candidate to compare against). Present in the schema, not "
            "faked or silently dropped, so a future real run fills them in without a schema migration. "
            f"positional_failure_flags is a crude stand-in (>0.15 Spearman gap vs. WR) for phase2 §3's real "
            "definition ('any position whose Spearman trails ADP's by more than the overall gap') -- the real "
            "definition needs spearman_overall to exist first. runs={runs}: every system registered today is "
            "deterministic (no LLM involved yet), so this mechanically proves the N-run/mean/range "
            "infrastructure (phase2 §5) rather than measuring real variance -- range will be [x,x] until a real "
            "nondeterministic agent exists.".format(runs=runs)
        ),
    }
    SCORECARDS_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = SCORECARDS_ROOT / f"{run_id}.json"
    out_path.write_text(json.dumps(scorecard, indent=2))
    print(f"[run_backtest] {run_id}: wrote {out_path}")
    print(f"  spearman_by_pos: {spearman_by_pos}")
    print(f"  spearman_overall_naive: {spearman_overall_naive}")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", required=True, choices=sorted(SYSTEM_REGISTRY))
    parser.add_argument("--world", required=True, dest="world_date", choices=sorted(baselines.WORLD_TO_SEASON))
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    try:
        run_backtest(args.system, args.world_date, args.runs)
    except SystemUnavailableError as exc:
        print(f"[run_backtest] SKIPPED -- {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
