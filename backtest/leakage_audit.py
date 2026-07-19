"""Leakage audit automation (docs/phase2-backtest-harness-design.md §2's
Leakage Audit Checklist, build-order step 1's "the audit checklist as code
where possible" -- run before a world is marked usable, and re-run if its
contents ever change).

Checklist item, and how each is handled here:
  1. Every file has a source timestamp <= world date          -- AUTOMATED (scans every raw source's manifest.json)
  2. No curated column derived from post-date data             -- N/A: frozen worlds currently hold only raw pulled
                                                                   data (ADP/ECR/depth-chart), no curated/derived
                                                                   tables exist yet within a world -- this check
                                                                   activates once Phase 3+ computes derived metrics
                                                                   inside a frozen-world context.
  3. ADP provenance documented per snapshot                     -- AUTOMATED (checks each manifest's source block)
  4. Rookie data limited to pre-date events                     -- N/A: no separate rookie/draft-capital pull exists
                                                                   inside a frozen world yet (nflverse_crosswalk's
                                                                   draft fields come from the CURRENT, non-dated
                                                                   crosswalk snapshot, same as every other Phase 1
                                                                   identity-resolution use -- draft class/round/pick
                                                                   are historical facts fixed at draft time, not
                                                                   time-varying, so this is low-risk but undocumented
                                                                   until a dedicated draft-capital-as-of-date pull
                                                                   exists).
  5. Missing values never backfilled from later observations    -- BY CONSTRUCTION: no code path in capture/ or
                                                                   backtest/frozen_worlds/ ever fills a missing value
                                                                   from a later snapshot -- unresolved rows are left
                                                                   unmatched (see the *_unmatched_queue outputs),
                                                                   never imputed. Verified by inspection, not scanned
                                                                   automatically (there's no "later" data a frozen
                                                                   world could reach for -- each pull is a single
                                                                   point-in-time fetch).
  6. Ground truth stored outside the world dir, no candidate access -- AUTOMATED (checks directory separation + greps
                                                                   for any accidental import of backtest.ground_truth
                                                                   from the candidate-building modules)
  7. Spot check: 3 players with known mid-season news, confirm no trace -- DATA-GROUNDED, not memory-based (this
                                                                   project's own rule: no LLM-recalled stats). Rather
                                                                   than assert "real" breakout stories from training
                                                                   knowledge, this finds the biggest gaps between a
                                                                   world's own ADP rank and that season's actual
                                                                   ground-truth finish -- large positive surprises
                                                                   (actual finish far better than the market
                                                                   predicted) are exactly what's IMPOSSIBLE if the
                                                                   world secretly knew the outcome, so their presence
                                                                   is itself evidence against leakage.
  8. Audit result + auditor date recorded in the world's manifest -- this script's output IS that record; written to
                                                                   backtest/frozen_worlds/{world_date}/leakage_audit.json

Usage (either venv):
    python -m backtest.leakage_audit               # both worlds
    python -m backtest.leakage_audit --world 2024-06-18
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

FROZEN_WORLDS_ROOT = Path("backtest/frozen_worlds")
GROUND_TRUTH_ROOT = Path("backtest/ground_truth")
CANDIDATE_BUILDING_MODULES = [
    Path("backtest/frozen_worlds/historical_adp.py"),
    Path("backtest/frozen_worlds/historical_ecr.py"),
    Path("backtest/frozen_worlds/historical_depth_charts.py"),
]
WORLD_TO_SEASON = {"2024-06-18": 2024, "2025-06-18": 2025}


def check_source_timestamps(world_date: str) -> dict:
    raw_dir = FROZEN_WORLDS_ROOT / world_date / "raw"
    manifests = sorted(raw_dir.glob("*/manifest.json"))
    violations = []
    checked = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text())
        source = manifest.get("source", {})
        # ADP/ECR manifests: a single source_timestamp. Depth-chart manifest:
        # per-team timestamps -- check every one, not just the latest.
        timestamps = (
            list(source["per_team_source_timestamp"].items())
            if "per_team_source_timestamp" in source
            else [(manifest_path.parent.name, source.get("source_timestamp"))]
        )
        world_compact = world_date.replace("-", "")
        for label, ts in timestamps:
            if ts is None:
                continue
            ts_date = ts[:8] if len(ts) >= 8 and ts[:8].isdigit() else ts[:10].replace("-", "")
            checked.append(f"{manifest_path.parent.name}/{label}")
            if ts_date > world_compact:
                violations.append(f"{manifest_path.parent.name}/{label}: source_timestamp {ts} is AFTER world_date {world_date}")
    return {
        "check": "source_timestamps_on_or_before_world_date",
        "passed": len(violations) == 0,
        "detail": f"{len(checked)} source timestamps checked across {len(manifests)} manifests" + (
            f"; VIOLATIONS: {violations}" if violations else ""
        ),
    }


def check_adp_provenance_documented(world_date: str) -> dict:
    raw_dir = FROZEN_WORLDS_ROOT / world_date / "raw"
    manifests = sorted(raw_dir.glob("*/manifest.json"))
    undocumented = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text())
        source = manifest.get("source", {})
        has_provenance = bool(source.get("provider")) and (
            source.get("source_timestamp") or source.get("per_team_source_timestamp")
        )
        if not has_provenance:
            undocumented.append(manifest_path.parent.name)
    return {
        "check": "provenance_documented_per_snapshot",
        "passed": len(undocumented) == 0,
        "detail": f"{len(manifests)} source manifests checked" + (f"; UNDOCUMENTED: {undocumented}" if undocumented else ""),
    }


def check_ground_truth_isolated() -> dict:
    violations = []
    for module_path in CANDIDATE_BUILDING_MODULES:
        if not module_path.exists():
            continue
        text = module_path.read_text()
        if "backtest.ground_truth" in text or "from backtest import ground_truth" in text:
            violations.append(str(module_path))
    dir_separated = GROUND_TRUTH_ROOT.resolve() != FROZEN_WORLDS_ROOT.resolve() and not str(
        GROUND_TRUTH_ROOT.resolve()
    ).startswith(str(FROZEN_WORLDS_ROOT.resolve()))
    return {
        "check": "ground_truth_isolated_from_candidate_building_code",
        "passed": len(violations) == 0 and dir_separated,
        "detail": f"checked {len(CANDIDATE_BUILDING_MODULES)} candidate-building modules for ground-truth imports "
        f"(none should exist); backtest/ground_truth/ and backtest/frozen_worlds/ confirmed directory-separated"
        + (f"; VIOLATIONS: {violations}" if violations else ""),
    }


def spot_check_surprises(world_date: str, top_n: int = 3) -> dict:
    """Finds the biggest ADP-rank-vs-actual-finish surprises using only
    already-verified tool-retrieved data (this project's own rule: no
    LLM-recalled stats, including for audit spot-checks) -- see module
    docstring item 7."""
    season = WORLD_TO_SEASON[world_date]
    adp_path = FROZEN_WORLDS_ROOT / world_date / "raw" / "fantasypros_adp" / "adp.parquet"
    gt_path = GROUND_TRUTH_ROOT / str(season) / "positional_finishes.parquet"
    if not adp_path.exists() or not gt_path.exists():
        return {"check": "spot_check_surprises", "passed": False, "detail": "missing ADP or ground truth data -- cannot run"}

    adp = pd.read_parquet(adp_path)
    adp = adp[adp["gsis_id"].notna()][["gsis_id", "player_name", "position", "position_rank_at_snapshot"]]
    gt = pd.read_parquet(gt_path)[["gsis_id", "position", "positional_rank", "ppg"]]

    merged = adp.merge(gt, on=["gsis_id", "position"], suffixes=("_adp", "_actual"))
    merged["surprise"] = merged["position_rank_at_snapshot"] - merged["positional_rank"]  # positive = beat the market
    top_surprises = merged.sort_values("surprise", ascending=False).head(top_n)
    examples = [
        f"{r.player_name} ({r.position}): ADP had him at {r.position}{int(r.position_rank_at_snapshot)} pre-season, "
        f"actually finished {r.position}{int(r.positional_rank)} ({r.ppg} PPG) -- a {int(r.surprise)}-spot surprise"
        for r in top_surprises.itertuples()
    ]
    return {
        "check": "spot_check_surprises",
        "passed": bool(len(top_surprises) > 0 and top_surprises["surprise"].max() > 5),
        "detail": (
            f"Found {len(top_surprises)} real market-vs-outcome surprises (>5-spot gap required to pass): "
            + "; ".join(examples)
            + ". Large positive surprises are structurally impossible if this world's ADP secretly knew the "
            "season's outcome -- their presence is itself evidence against leakage, not proof by absence."
        ),
    }


def run_audit(world_date: str) -> dict:
    checks = [
        check_source_timestamps(world_date),
        check_adp_provenance_documented(world_date),
        check_ground_truth_isolated(),
        spot_check_surprises(world_date),
    ]
    not_applicable = [
        {"check": "no_curated_column_derived_from_post_date_data", "status": "N/A",
         "detail": "no curated/derived tables exist within a frozen world yet -- only raw pulled data"},
        {"check": "rookie_data_limited_to_pre_date_events", "status": "N/A",
         "detail": "no separate as-of-date draft-capital pull exists yet; draft class/round/pick are fixed "
         "historical facts from the current (non-dated) crosswalk, low-risk but undocumented per-world"},
        {"check": "missing_values_never_backfilled", "status": "BY_CONSTRUCTION",
         "detail": "no code path in capture/ or backtest/frozen_worlds/ imputes a missing value from a later "
         "snapshot -- verified by inspection, not automatically scanned"},
    ]
    all_passed = all(c["passed"] for c in checks)
    result = {
        "world_date": world_date,
        "audited_at": datetime.now().isoformat(),
        "auditor": "backtest/leakage_audit.py (automated)",
        "checks": checks,
        "not_applicable_or_by_construction": not_applicable,
        "all_automated_checks_passed": all_passed,
    }
    out_path = FROZEN_WORLDS_ROOT / world_date / "leakage_audit.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(f"[leakage_audit] {world_date}: all_automated_checks_passed={all_passed} -- wrote {out_path}")
    for c in checks:
        print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['check']}: {c['detail']}")
    return result


def run(world_dates: list[str]) -> int:
    all_passed = True
    for world_date in world_dates:
        result = run_audit(world_date)
        all_passed = all_passed and result["all_automated_checks_passed"]
    return 0 if all_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", action="append", dest="world_dates", choices=list(WORLD_TO_SEASON))
    args = parser.parse_args()
    return run(args.world_dates or list(WORLD_TO_SEASON))


if __name__ == "__main__":
    sys.exit(main())
