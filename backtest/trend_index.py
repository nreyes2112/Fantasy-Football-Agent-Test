"""Trend index (docs/phase2-backtest-harness-design.md §6, build-order step
5's remaining piece) -- "a small index builds the trend view: metric
trajectories across config versions per world ... trend beats absolute, the
question is always 'did this change help?'".

Scans every scorecard in backtest/scorecards/ (both the Baseline Bank's
fixed-name scorecards from backtest/baselines.py and run_backtest.py's
timestamped ones) and builds one row per scorecard, sorted so each
(system, world) group reads as a trajectory over generated_at. With today's
data that trajectory is length 1 per (system, world) for most systems --
the index itself is the useful artifact, not a claim that trends exist yet.

Usage (either venv):
    python -m backtest.trend_index
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

SCORECARDS_ROOT = Path("backtest/scorecards")


def build_trend_index() -> pd.DataFrame:
    rows = []
    for path in sorted(SCORECARDS_ROOT.glob("*.json")):
        card = json.loads(path.read_text())
        headline = card.get("headline", {})
        spearman_overall_naive = headline.get("spearman_overall_naive")
        # run_backtest.py's mean/range wrapper vs. baselines.py's plain scalar -- normalize both to a single number.
        if isinstance(spearman_overall_naive, dict):
            spearman_overall_naive = spearman_overall_naive.get("mean")
        by_pos = headline.get("spearman_by_pos", {}) or {}

        def _pos_value(pos_entry):
            if pos_entry is None:
                return None
            val = pos_entry.get("spearman", pos_entry.get("mean"))
            return val

        rows.append(
            {
                "run_id": card.get("run_id"),
                "system": card.get("system"),
                "world": card.get("world"),
                "config_hash": card.get("config_hash"),
                "code_git_commit": card.get("code_git_commit"),
                "generated_at": card.get("generated_at"),
                "runs": card.get("runs"),
                "spearman_overall_naive": spearman_overall_naive,
                "spearman_QB": _pos_value(by_pos.get("QB")),
                "spearman_RB": _pos_value(by_pos.get("RB")),
                "spearman_WR": _pos_value(by_pos.get("WR")),
                "spearman_TE": _pos_value(by_pos.get("TE")),
                "scorecard_path": str(path),
            }
        )
    df = pd.DataFrame.from_records(rows)
    if len(df):
        df = df.sort_values(["system", "world", "generated_at"]).reset_index(drop=True)
    return df


def write_trend_index() -> Path:
    df = build_trend_index()
    out_path = SCORECARDS_ROOT / "trend_index.json"
    df.to_json(out_path, orient="records", indent=2)
    print(f"[trend_index] {len(df)} scorecards indexed across {df['system'].nunique() if len(df) else 0} systems "
          f"-- wrote {out_path}")
    return out_path


def main() -> int:
    write_trend_index()
    return 0


if __name__ == "__main__":
    sys.exit(main())
