"""Weekly canonical-ID crosswalk build (phase1-data-platform-design.md §3, §8).

Reads today's already-captured raw tables (Sleeper, ESPN, FFC -- from
capture/pull_daily.py) plus a fresh pull of the nflverse/DynastyProcess ID
crosswalk, resolves Sleeper and ESPN player ids to canonical gsis_id
deterministically, and *proposes* FFC name matches for human confirmation
(never auto-confirmed -- see capture/crosswalk.py).

Writes into TODAY's existing dated snapshot under curated/, with its own
manifest (curated_manifest.json) so the original raw/manifest.json from
pull_daily.py is never touched after the fact (§4 immutability).

Requires nflreadpy, which needs Python >= 3.10 -- run this under .venv311,
not the project's original .venv (created for Python 3.9).

Usage:
    python -m capture.pull_crosswalk
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from capture.config import LEAGUE_TIMEZONE, SCHEMA_ROOT, SNAPSHOT_ROOT
from capture.crosswalk import charter_universe_coverage, propose_by_name, resolve_source
from capture.sources import nflverse

# Charter's fixed player universe (charter.md §5), used as the acceptance
# metric's denominator.
CHARTER_UNIVERSE_SIZES = {"QB": 24, "RB": 48, "WR": 60, "TE": 24}

_SCHEMA_NOTES = {
    "nflverse_crosswalk": {
        "gsis_id": "canonical key (nflverse). Everything else in this project resolves to this.",
        "sleeper_id": "join key against sleeper/players.parquet's player_id",
        "espn_id": "join key against espn/player_pool.parquet's player_id",
        "merge_name": "pre-normalized name, used only as a last-resort proposal key (§3) -- never auto-joined",
        "...": "35 columns total from nflreadpy's load_ff_playerids() (DynastyProcess.com); all other columns kept as-is",
    },
    "sleeper_resolved": {
        "gsis_id": "resolved via nflverse_crosswalk.sleeper_id; null = not found in this week's crosswalk",
        "gsis_id_source_reported": "Sleeper's own self-reported gsis_id field, if present -- NOT authoritative, kept only for cross-checking",
    },
    "espn_resolved": {
        "gsis_id": "resolved via nflverse_crosswalk.espn_id; null = not found in this week's crosswalk",
    },
    "ffc_proposed_matches": {
        "gsis_id": "PROPOSED via normalized-name match against merge_name -- NOT a confirmed crosswalk entry (§3); ambiguous names (>1 gsis_id sharing a normalized name) are excluded, not guessed",
    },
    "ffc_unmatched_queue": {
        "note": "FFC rows with no confident name proposal -- needs human review before any agent run treats FFC data as identity-resolved",
    },
}


def _write_schema_once(table: str, columns: dict) -> None:
    schema_dir = Path(SCHEMA_ROOT) / table
    schema_dir.mkdir(parents=True, exist_ok=True)
    path = schema_dir / "v1.json"
    if not path.exists():
        path.write_text(json.dumps({"table": table, "version": "v1", "columns": columns}, indent=2))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True)
        return out.stdout.strip()
    except Exception:
        return None


def run() -> int:
    today = datetime.now(LEAGUE_TIMEZONE).strftime("%Y-%m-%d")
    snapshot_dir = Path(SNAPSHOT_ROOT) / today
    raw_dir = snapshot_dir / "raw"
    curated_dir = snapshot_dir / "curated"

    if not (snapshot_dir / "manifest.json").exists():
        raise SystemExit(
            f"No raw snapshot for {today} yet -- run `python -m capture.pull_daily` (under .venv) first."
        )

    print("[crosswalk] loading today's raw tables...")
    sleeper_df = pd.read_parquet(raw_dir / "sleeper" / "players.parquet")
    espn_df = pd.read_parquet(raw_dir / "espn" / "player_pool.parquet")
    ffc_df = pd.read_parquet(raw_dir / "ffc" / "adp.parquet")

    print("[crosswalk] pulling nflverse/DynastyProcess ff_playerids crosswalk...")
    nflverse_df = nflverse.fetch_ff_playerids()

    sleeper_resolved, sleeper_stats = resolve_source(sleeper_df, "player_id", nflverse_df, "sleeper_id")
    espn_resolved, espn_stats = resolve_source(espn_df, "player_id", nflverse_df, "espn_id")
    ffc_proposed, ffc_unmatched, ffc_stats = propose_by_name(ffc_df, "name", nflverse_df, "merge_name")

    coverage = charter_universe_coverage(
        espn_resolved,
        CHARTER_UNIVERSE_SIZES,
        other_sources={"sleeper": sleeper_resolved, "ffc_proposed": ffc_proposed},
    )

    curated_dir.mkdir(parents=True, exist_ok=True)
    files = []

    def write(name: str, df: pd.DataFrame) -> None:
        path = curated_dir / f"{name}.parquet"
        df.to_parquet(path, index=False)
        files.append(
            {
                "table": name,
                "path": str(path.relative_to(Path("."))),
                "row_count": len(df),
                "sha256": _sha256(path),
            }
        )

    write("nflverse_crosswalk", nflverse_df)
    write("sleeper_resolved", sleeper_resolved)
    write("espn_resolved", espn_resolved)
    write("ffc_proposed_matches", ffc_proposed)
    write("ffc_unmatched_queue", ffc_unmatched)
    for table, columns in _SCHEMA_NOTES.items():
        _write_schema_once(table, columns)

    report = {
        "snapshot_date": today,
        "generated_at": datetime.now(LEAGUE_TIMEZONE).isoformat(),
        "code_git_commit": _git_commit(),
        "resolution_stats": {
            "sleeper": sleeper_stats,
            "espn": espn_stats,
            "ffc": ffc_stats,
        },
        "charter_universe_coverage": coverage,
        "files": files,
    }
    manifest_path = snapshot_dir / "curated_manifest.json"
    manifest_path.write_text(json.dumps(report, indent=2))

    print(f"[crosswalk] Sleeper resolved (all {sleeper_stats['total_rows']} players in Sleeper's dump): {sleeper_stats['coverage_pct']}%")
    print(f"[crosswalk] ESPN resolved (all {espn_stats['total_rows']} players in the pool): {espn_stats['coverage_pct']}%")
    print(f"[crosswalk] FFC proposed (name match, needs confirmation): {ffc_stats['proposed_pct']}%")
    print("[crosswalk] charter universe coverage -- SAME players resolved across ALL sources (ESPN ADP order as consensus proxy):")
    all_full = True
    for row in coverage:
        print(
            f"  {row['position']}: {row['fully_resolved_across_all_sources']}/{row['universe_size']} fully resolved "
            f"({row['coverage_pct']}%) -- espn={row['espn_resolved']} sleeper={row['sleeper_found']} ffc_proposed={row['ffc_proposed_found']}"
        )
        if row["coverage_pct"] < 100.0:
            all_full = False
    print(f"[crosswalk] wrote {manifest_path}")
    print(f"[crosswalk] unmatched FFC queue: {len(ffc_unmatched)} rows -- needs human review before any agent run")

    return 0 if all_full else 1


if __name__ == "__main__":
    sys.exit(run())
