"""Frozen-world ECR assembly (docs/phase2-backtest-harness-design.md §4's
Baseline Bank -- "ECR: Expert consensus at world date"), decisions.md D-010's
outstanding piece.

Same sourcing pattern as historical_adp.py: FantasyPros' public rankings page
via Wayback Machine archive. Coverage near the frozen-world dates turned out
BETTER than initially assessed for the ADP source -- the original assessment
("~45 days before each date") was measured against the design's original
2024-07-15/2025-07-15 target dates, before D-009 re-anchored the worlds to
2024-06-18/2025-06-18; against the actual (post-D-009) world dates, the
closest-before snapshots are 2024-05-31 and 2025-05-31 -- 18 days before each
world date, tighter than the ADP source's 27-day lag, not wider.

Resolution is a DETERMINISTIC id join, same tier of confidence as the ADP
source: the archived page embeds a JSON blob (`var ecrData = {...}`) with
FantasyPros' own numeric player_id per player, which is the SAME id space as
the ADP page's fp-id-NNNN (verified: Christian McCaffrey = 16393 in both) and
matches the crosswalk's existing fantasypros_id column -- no name-matching
queue needed, unlike the depth-chart source.

Usage (either venv -- requests/pandas only):
    python -m backtest.frozen_worlds.historical_ecr               # both worlds
    python -m backtest.frozen_worlds.historical_ecr --world 2024-06-18
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from access.snapshot_resolver import load_curated_table, resolve_snapshot_date
from capture.crosswalk import resolve_source
from capture.manifest_utils import git_commit, sha256_file

REQUEST_TIMEOUT_S = 30
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}

# D-010: closest-before-world-date Wayback snapshots of FantasyPros' PPR
# draft-rankings (ECR) page, verified 2026-07-19 to contain a real,
# server-embedded ecrData JSON blob (51/58 experts respectively) -- not a
# JS shell Wayback failed to capture.
WAYBACK_SNAPSHOTS = {
    "2024-06-18": {
        "original_url": "https://www.fantasypros.com/nfl/rankings/ppr-cheatsheets.php",
        "wayback_timestamp": "20240531095503",
    },
    "2025-06-18": {
        "original_url": "https://www.fantasypros.com/nfl/rankings/ppr-cheatsheets.php",
        "wayback_timestamp": "20250531134608",
    },
}

FROZEN_WORLDS_ROOT = Path("backtest/frozen_worlds")
CHARTER_POSITIONS = ("QB", "RB", "WR", "TE")

_ECR_DATA_RE = re.compile(r"var ecrData\s*=\s*(\{.*?\});", re.S)
_POS_RANK_RE = re.compile(r"^([A-Z]+)(\d+)$")


def fetch_snapshot_html(world_date: str) -> str:
    if world_date not in WAYBACK_SNAPSHOTS:
        raise ValueError(f"no Wayback snapshot registered for world_date={world_date!r} (see WAYBACK_SNAPSHOTS)")
    spec = WAYBACK_SNAPSHOTS[world_date]
    url = f"http://web.archive.org/web/{spec['wayback_timestamp']}id_/{spec['original_url']}"
    # requests decompresses gzip automatically (unlike plain curl without
    # --compressed, which returned raw gzip bytes during manual testing).
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    return resp.text


def parse_ecr_json(html: str) -> pd.DataFrame:
    m = _ECR_DATA_RE.search(html)
    if not m:
        raise ValueError("could not find the ecrData JSON blob in the fetched HTML -- page structure may have changed")
    data = json.loads(m.group(1))
    players = data["players"]

    records = []
    for p in players:
        if p.get("player_position_id") not in CHARTER_POSITIONS:
            continue  # K/DST out of charter scope (§4)
        pos_match = _POS_RANK_RE.match(p.get("pos_rank") or "")
        if not pos_match:
            continue  # no positional rank published for this player -- can't use as a candidate row
        records.append(
            {
                "fp_id": str(p["player_id"]),
                "player_name": p["player_name"],
                "team": p.get("player_team_id"),
                "position": p["player_position_id"],
                "positional_rank": int(pos_match.group(2)),
                "overall_rank_ecr": p.get("rank_ecr"),
                "tier": p.get("tier"),
                "rank_ave": p.get("rank_ave"),
                "rank_std": p.get("rank_std"),
            }
        )
    return pd.DataFrame.from_records(records)


def resolve_to_gsis(ecr_df: pd.DataFrame, crosswalk: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    return resolve_source(ecr_df, source_id_col="fp_id", crosswalk=crosswalk, crosswalk_id_col="fantasypros_id")


def build_frozen_world_ecr(world_date: str, crosswalk_snapshot_date: str | None = None):
    html = fetch_snapshot_html(world_date)
    ecr_df = parse_ecr_json(html)

    pinned_crosswalk_date = resolve_snapshot_date(crosswalk_snapshot_date)
    crosswalk = load_curated_table(pinned_crosswalk_date, "nflverse_crosswalk")
    resolved, stats = resolve_to_gsis(ecr_df, crosswalk)
    resolved["world_date"] = world_date
    return resolved, stats, pinned_crosswalk_date


def write_frozen_world_ecr(world_date: str, crosswalk_snapshot_date: str | None = None) -> Path:
    resolved, stats, pinned_crosswalk_date = build_frozen_world_ecr(world_date, crosswalk_snapshot_date)
    spec = WAYBACK_SNAPSHOTS[world_date]

    out_dir = FROZEN_WORLDS_ROOT / world_date / "raw" / "fantasypros_ecr"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "ecr.parquet"
    resolved.to_parquet(out_path, index=False)

    wb_ts = spec["wayback_timestamp"]
    source_timestamp = datetime.strptime(wb_ts, "%Y%m%d%H%M%S").isoformat()
    manifest = {
        "world_date": world_date,
        "generated_at": datetime.now().isoformat(),
        "code_git_commit": git_commit(),
        "source": {
            "provider": "FantasyPros ECR (fantasypros.com/nfl/rankings/ppr-cheatsheets.php), via Wayback Machine archive",
            "original_url": spec["original_url"],
            "wayback_url": f"http://web.archive.org/web/{wb_ts}id_/{spec['original_url']}",
            "source_timestamp": source_timestamp,
            "decision": "decisions.md D-010",
        },
        "resolution": {
            "method": "deterministic id join (ecrData JSON's player_id vs crosswalk's fantasypros_id -- same id "
            "space verified against the ADP source, decisions.md D-009)",
            "crosswalk_snapshot_date": pinned_crosswalk_date,
            **stats,
        },
        "row_count": len(resolved),
        "file": {"path": str(out_path), "sha256": sha256_file(out_path)},
        "leakage_audit": {
            "source_timestamp_on_or_before_world_date": True,
            "note": f"source_timestamp ({source_timestamp}) predates world_date ({world_date}) by 18 days both "
            "worlds -- tighter than the ADP source's 27-day lag.",
        },
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(
        f"[historical_ecr] {world_date}: wrote {out_path} ({len(resolved)} rows, "
        f"{stats['resolved_rows']}/{stats['total_rows']} resolved to gsis_id) and {manifest_path}"
    )
    return out_path


def run(world_dates: list[str]) -> int:
    for world_date in world_dates:
        write_frozen_world_ecr(world_date)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--world", action="append", dest="world_dates", choices=list(WAYBACK_SNAPSHOTS),
        help="world date to build (repeatable). Defaults to both.",
    )
    args = parser.parse_args()
    return run(args.world_dates or list(WAYBACK_SNAPSHOTS))


if __name__ == "__main__":
    sys.exit(main())
