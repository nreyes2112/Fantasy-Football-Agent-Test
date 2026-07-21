"""Agent run harness (docs/phase3-agent-prompts.md Integration Notes 2-4 +
phase2 §5's 3-run protocol). The LLM step itself executes inside a Claude
Code session (D-006); this module manages everything around it:

  emit-prompt  write the exact prompt artifact + pool for a (world,
               position, run) into the run directory
  ingest       validate an agent's JSON output (schema, pool coverage,
               rank integrity, citation discipline incl. no
               post-world-date evidence) and store it
  assemble     merge a run's per-position outputs into the
               [gsis_id, position, positional_rank, overall_rank]
               candidate table backtest/run_backtest.py scores

Run layout: backtest/agent_runs/{world}/{agent_id}/{prompt_version}/run{K}/
  prompt_{POS}.txt, pool_{POS}.json   (emit-prompt)
  {POS}.json                          (ingest)
  candidate.parquet                   (assemble)

Stored OUTSIDE backtest/frozen_worlds/ (nothing under a world dir is ever
edited after its audit) and with no ground-truth access (this module is
candidate-building code and is included in leakage_audit.py's isolation
check).

Usage (either venv):
    python -m agents.runner emit-prompt --world 2025-06-18 --position RB --run 1
    python -m agents.runner ingest      --world 2025-06-18 --position RB --run 1 --file /path/out.json
    python -m agents.runner assemble    --world 2025-06-18 --run 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from agents.pool import build_player_pool
from agents.prompts import PROMPT_VERSIONS, build_prompt
from backtest.scoring import SM2_UNIVERSE_SIZES

AGENT_RUNS_ROOT = Path("backtest/agent_runs")
DEFAULT_AGENT = "opportunity_analyst"

# Sanity bounds for validation, not projections: no QB/RB/WR/TE averages
# outside these in this league's scoring era (ground truth 2024/2025 maxima
# are ~26-27 PPG).
_PPG_BOUNDS = (0.0, 40.0)
_SEASON_GAMES_BOUNDS = (10, 17)  # implied games = season_total / ppg


def run_dir(world_date: str, run_num: int, agent_id: str = DEFAULT_AGENT) -> Path:
    version = PROMPT_VERSIONS[agent_id]
    return AGENT_RUNS_ROOT / world_date / agent_id / version / f"run{run_num}"


def emit_prompt(world_date: str, position: str, run_num: int, agent_id: str = DEFAULT_AGENT) -> Path:
    pool = build_player_pool(world_date, position)
    prompt = build_prompt(agent_id, position, world_date, pool)
    out = run_dir(world_date, run_num, agent_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"prompt_{position}.txt").write_text(prompt)
    pool.to_json(out / f"pool_{position}.json", orient="records", indent=2)
    print(f"[agents.runner] wrote {out / f'prompt_{position}.txt'} ({len(pool)} players in pool)")
    return out / f"prompt_{position}.txt"


def validate_output(doc: dict, world_date: str, position: str, pool: pd.DataFrame,
                    agent_id: str = DEFAULT_AGENT) -> list[str]:
    """Returns a list of violations (empty = valid). Enforces the OUTPUT
    FORMAT schema plus the harness-level integrity rules the schema alone
    can't express: full pool coverage, clean rank sequence, and the citation
    discipline (every player cited, no evidence dated after the world)."""
    errors = []
    if doc.get("agent_id") != agent_id:
        errors.append(f"agent_id {doc.get('agent_id')!r} != expected {agent_id!r}")
    if doc.get("position") != position:
        errors.append(f"position {doc.get('position')!r} != expected {position!r}")
    if doc.get("snapshot_date") != world_date:
        errors.append(f"snapshot_date {doc.get('snapshot_date')!r} != world {world_date!r}")
    if not isinstance(doc.get("data_gaps"), list):
        errors.append("data_gaps missing or not a list")

    rankings = doc.get("rankings")
    if not isinstance(rankings, list) or not rankings:
        return errors + ["rankings missing/empty"]

    pool_ids = set(pool["gsis_id"])
    seen_ids = [r.get("player_id") for r in rankings]
    if set(seen_ids) != pool_ids:
        missing = sorted(pool_ids - set(seen_ids))
        extra = sorted(set(seen_ids) - pool_ids)
        errors.append(f"pool coverage violated -- missing {len(missing)}: {missing[:5]}; extra {len(extra)}: {extra[:5]}")
    if len(seen_ids) != len(set(seen_ids)):
        errors.append("duplicate player_id in rankings")
    ranks = [r.get("rank") for r in rankings]
    if sorted(ranks) != list(range(1, len(rankings) + 1)):
        errors.append(f"ranks are not a clean 1..{len(rankings)} sequence")

    name_by_id = pool.set_index("gsis_id")["player_name"].to_dict()
    for r in rankings:
        rid = f"rank {r.get('rank')} ({r.get('player')})"
        ppg = r.get("proj_ppg")
        total = r.get("proj_season_total")
        conf = r.get("confidence")
        if not isinstance(ppg, (int, float)) or not (_PPG_BOUNDS[0] < ppg < _PPG_BOUNDS[1]):
            errors.append(f"{rid}: proj_ppg {ppg!r} outside sane bounds {_PPG_BOUNDS}")
        elif isinstance(total, (int, float)) and ppg > 0:
            implied_games = total / ppg
            if not (_SEASON_GAMES_BOUNDS[0] <= implied_games <= _SEASON_GAMES_BOUNDS[1]):
                errors.append(f"{rid}: season_total/ppg implies {implied_games:.1f} games, outside {_SEASON_GAMES_BOUNDS}")
        if not isinstance(total, (int, float)) or total <= 0:
            errors.append(f"{rid}: proj_season_total {total!r} invalid")
        if not isinstance(conf, (int, float)) or not (0 < conf <= 1):
            errors.append(f"{rid}: confidence {conf!r} not in (0, 1]")
        if not str(r.get("rationale") or "").strip():
            errors.append(f"{rid}: empty rationale")
        if not str(r.get("what_would_change_my_mind") or "").strip():
            errors.append(f"{rid}: empty what_would_change_my_mind (pre-registered falsifier is mandatory)")
        if r.get("player_id") in name_by_id and str(r.get("player") or "").strip() == "":
            errors.append(f"{rid}: empty player name")
        evidence = r.get("evidence")
        if not isinstance(evidence, list) or len(evidence) == 0:
            errors.append(f"{rid}: no evidence citations (uncited claims are invalid)")
            continue
        for ev in evidence:
            if not all(str(ev.get(k, "") or "").strip() for k in ("metric", "value", "source", "snapshot_date")):
                errors.append(f"{rid}: evidence item missing metric/value/source/snapshot_date: {ev}")
            elif str(ev["snapshot_date"]) > world_date:
                errors.append(f"{rid}: evidence dated {ev['snapshot_date']} AFTER world {world_date} -- leakage")
    return errors


def ingest(world_date: str, position: str, run_num: int, file_path: str,
           agent_id: str = DEFAULT_AGENT) -> Path:
    doc = json.loads(Path(file_path).read_text())
    pool = build_player_pool(world_date, position)
    errors = validate_output(doc, world_date, position, pool, agent_id)
    if errors:
        for e in errors:
            print(f"  [INVALID] {e}")
        raise SystemExit(f"[agents.runner] REJECTED {file_path}: {len(errors)} violation(s) -- nothing stored")
    out = run_dir(world_date, run_num, agent_id)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"{position}.json"
    dest.write_text(json.dumps(doc, indent=2))
    print(f"[agents.runner] VALID -- stored {dest} ({len(doc['rankings'])} rankings)")
    return dest


def assemble(world_date: str, run_num: int, agent_id: str = DEFAULT_AGENT) -> Path:
    """Merge whichever positions this run has produced into the candidate
    shape. positional_rank comes straight from the agent; overall_rank is a
    NAIVE cross-position ordering by proj_season_total, carrying exactly the
    same caveat as spearman_overall_naive everywhere else in this project
    (not VORP-adjusted; per-position numbers are the trustworthy signal)."""
    out = run_dir(world_date, run_num, agent_id)
    frames = []
    for position in SM2_UNIVERSE_SIZES:
        pos_file = out / f"{position}.json"
        if not pos_file.exists():
            continue
        doc = json.loads(pos_file.read_text())
        frames.append(pd.DataFrame([
            {"gsis_id": r["player_id"], "position": position, "positional_rank": r["rank"],
             "proj_season_total": r["proj_season_total"]}
            for r in doc["rankings"]
        ]))
    if not frames:
        raise SystemExit(f"[agents.runner] no ingested position outputs under {out} -- nothing to assemble")
    candidate = pd.concat(frames, ignore_index=True)
    candidate["overall_rank"] = candidate["proj_season_total"].rank(ascending=False, method="first").astype(int)
    candidate = candidate.drop(columns=["proj_season_total"])
    dest = out / "candidate.parquet"
    candidate.to_parquet(dest, index=False)
    positions = sorted(candidate["position"].unique())
    print(f"[agents.runner] wrote {dest} -- {len(candidate)} players across {positions}")
    return dest


def load_agent_candidate(world_date: str, run_index: int, agent_id: str = DEFAULT_AGENT) -> pd.DataFrame:
    """Loader for backtest/run_backtest.py: run_index i -> stored run{i+1}.
    Unlike the deterministic baselines, each of the N runs is a DIFFERENT
    stored LLM output -- re-running the loader can't and shouldn't re-invoke
    the LLM (D-006: agent runs happen in Claude Code sessions, the harness
    only scores what was stored and validated)."""
    path = run_dir(world_date, run_index + 1, agent_id) / "candidate.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist -- run{run_index + 1} hasn't been executed/ingested/assembled yet "
            f"(see agents/runner.py usage)"
        )
    return pd.read_parquet(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("emit-prompt", "ingest", "assemble"):
        p = sub.add_parser(name)
        p.add_argument("--world", required=True)
        p.add_argument("--run", type=int, required=True)
        p.add_argument("--agent", default=DEFAULT_AGENT)
        if name != "assemble":
            p.add_argument("--position", required=True, choices=sorted(SM2_UNIVERSE_SIZES))
        if name == "ingest":
            p.add_argument("--file", required=True)
    args = parser.parse_args()
    if args.command == "emit-prompt":
        emit_prompt(args.world, args.position, args.run, args.agent)
    elif args.command == "ingest":
        ingest(args.world, args.position, args.run, args.file, args.agent)
    elif args.command == "assemble":
        assemble(args.world, args.run, args.agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
