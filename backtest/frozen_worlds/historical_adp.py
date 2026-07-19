"""Frozen-world ADP assembly (docs/phase2-backtest-harness-design.md §2,
build-order step 1's ADP component) -- decisions.md D-009.

Our own daily capture (capture/sources/ffc_adp.py) didn't exist in 2024/2025,
and its live API has no as-of-date parameter (D-009's research: `year=2024`
returns only a ~2-day pre-season aggregate window, not a July snapshot). The
only genuinely dated historical source found is the Wayback Machine's archive
of FantasyPros' public ADP page (fantasypros.com/nfl/adp/ppr-overall.php),
which has real coverage close to (not on) the original 2024-07-15/2025-07-15
target dates -- hence D-009 re-anchored the frozen worlds to the dates that
data actually exists for: 2024-06-18 and 2025-06-18.

Resolution to gsis_id is a DETERMINISTIC id join, not a name-matching
proposal: the archived HTML embeds FantasyPros' own player id per row
(`fp-id-NNNN`), and the nflverse/DynastyProcess crosswalk carries a matching
`fantasypros_id` column (verified 2026-07-18: Christian McCaffrey = 16393 in
both) -- same confidence tier as the Sleeper/ESPN joins, reusing
capture/crosswalk.py's resolve_source() rather than propose_by_name()'s
human-review queue.

Usage (either venv -- no nflreadpy dependency, just requests/pandas):
    python -m backtest.frozen_worlds.historical_adp               # both worlds
    python -m backtest.frozen_worlds.historical_adp --world 2024-06-18
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

# D-009: the only two Wayback snapshots found with real coverage near the
# original 2024-07-15/2025-07-15 target dates. `wayback_timestamp` is the
# exact capture instant (source_timestamp for the leakage audit); world_date
# is the date this project's frozen world is now keyed under (D-009).
WAYBACK_SNAPSHOTS = {
    "2024-06-18": {
        "original_url": "https://www.fantasypros.com/nfl/adp/ppr-overall.php",
        "wayback_timestamp": "20240618215630",
    },
    "2025-06-18": {
        "original_url": "https://www.fantasypros.com/nfl/adp/ppr-overall.php",
        "wayback_timestamp": "20250618085726",
    },
}

FROZEN_WORLDS_ROOT = Path("backtest/frozen_worlds")

# One row per player: rank cell, then the player-label cell (fp id + name,
# optional team + optional bye -- free agents at snapshot time have neither),
# then POS (e.g. "RB1") and AVG (the ADP value) cells. Verified 2026-07-18
# against the 2024-06-18 snapshot: matches 424/425 table rows (the 1 miss is
# the header row, expected).
_ROW_RE = re.compile(
    r"<td>(\d+)</td>\s*"
    r'<td[^>]*><a[^>]*fp-id-(\d+)"[^>]*fp-player-name="([^"]+)"[^>]*>.*?</a>'
    r"(?:\s*<small>([A-Za-z]{1,3})</small>)?(?:\s*<small>\((\w+)\)</small>)?</td>\s*"
    r"<td>([A-Z]+)(\d+)</td><td>([\d.]+)</td>",
    re.S,
)


def fetch_snapshot_html(world_date: str) -> str:
    if world_date not in WAYBACK_SNAPSHOTS:
        raise ValueError(f"no Wayback snapshot registered for world_date={world_date!r} (see WAYBACK_SNAPSHOTS)")
    spec = WAYBACK_SNAPSHOTS[world_date]
    # the "id_" modifier serves the raw archived HTML with Wayback's own
    # toolbar/link-rewriting stripped out -- easier to parse, still the exact
    # bytes the server returned at capture time.
    url = f"http://web.archive.org/web/{spec['wayback_timestamp']}id_/{spec['original_url']}"
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    return resp.text


def parse_adp_table(html: str) -> pd.DataFrame:
    table_match = re.search(r'<table[^>]*id="data".*?</table>', html, re.S)
    if not table_match:
        raise ValueError("could not find the ADP table (id=\"data\") in the fetched HTML -- page structure may have changed")
    row_html_blocks = re.findall(r"<tr>(.*?)</tr>", table_match.group(0), re.S)

    records = []
    unparsed = 0
    for block in row_html_blocks:
        m = _ROW_RE.search(block)
        if not m:
            unparsed += 1  # expected: exactly 1, the header row
            continue
        rank, fp_id, player_name, team, bye, position, position_rank, adp = m.groups()
        records.append(
            {
                "rank": int(rank),
                "fp_id": fp_id,
                "player_name": player_name,
                "team": team,  # None for free agents at snapshot time
                "bye": int(bye) if bye and bye.isdigit() else None,
                "position": position,
                "position_rank_at_snapshot": int(position_rank),
                "adp": float(adp),
            }
        )
    if unparsed > 1:
        raise ValueError(f"{unparsed} table rows failed to parse (expected exactly 1, the header) -- page structure may have changed")
    return pd.DataFrame.from_records(records)


def resolve_to_gsis(adp_df: pd.DataFrame, crosswalk: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    return resolve_source(adp_df, source_id_col="fp_id", crosswalk=crosswalk, crosswalk_id_col="fantasypros_id")


def build_frozen_world_adp(world_date: str, crosswalk_snapshot_date: str | None = None) -> pd.DataFrame:
    html = fetch_snapshot_html(world_date)
    adp_df = parse_adp_table(html)

    pinned_crosswalk_date = resolve_snapshot_date(crosswalk_snapshot_date)
    crosswalk = load_curated_table(pinned_crosswalk_date, "nflverse_crosswalk")
    resolved, stats = resolve_to_gsis(adp_df, crosswalk)
    resolved["world_date"] = world_date
    return resolved, stats, pinned_crosswalk_date


def write_frozen_world_adp(world_date: str, crosswalk_snapshot_date: str | None = None) -> Path:
    resolved, stats, pinned_crosswalk_date = build_frozen_world_adp(world_date, crosswalk_snapshot_date)
    spec = WAYBACK_SNAPSHOTS[world_date]

    out_dir = FROZEN_WORLDS_ROOT / world_date / "raw" / "fantasypros_adp"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "adp.parquet"
    resolved.to_parquet(out_path, index=False)

    wb_ts = spec["wayback_timestamp"]
    source_timestamp = datetime.strptime(wb_ts, "%Y%m%d%H%M%S").isoformat()
    manifest = {
        "world_date": world_date,
        "generated_at": datetime.now().isoformat(),
        "code_git_commit": git_commit(),
        "source": {
            "provider": "FantasyPros ADP (fantasypros.com/nfl/adp/ppr-overall.php), via Wayback Machine archive",
            "original_url": spec["original_url"],
            "wayback_url": f"http://web.archive.org/web/{wb_ts}id_/{spec['original_url']}",
            "source_timestamp": source_timestamp,
            "decision": "decisions.md D-009",
        },
        "resolution": {
            "method": "deterministic id join (fp-id embedded in archived HTML vs crosswalk's fantasypros_id)",
            "crosswalk_snapshot_date": pinned_crosswalk_date,
            **stats,
        },
        "row_count": len(resolved),
        "file": {"path": str(out_path), "sha256": sha256_file(out_path)},
        "leakage_audit": {
            "source_timestamp_on_or_before_world_date": True,
            "note": f"source_timestamp ({source_timestamp}) predates world_date ({world_date}) -- world_date IS the "
            "snapshot date (D-009 re-anchored the world to the data, not the reverse), so this is trivially satisfied "
            "by construction. Still recorded explicitly per the phase2 leakage checklist's first line item.",
        },
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(
        f"[historical_adp] {world_date}: wrote {out_path} ({len(resolved)} rows, "
        f"{stats['resolved_rows']}/{stats['total_rows']} resolved to gsis_id) and {manifest_path}"
    )
    return out_path


def run(world_dates: list[str]) -> int:
    for world_date in world_dates:
        write_frozen_world_adp(world_date)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--world", action="append", dest="world_dates", choices=list(WAYBACK_SNAPSHOTS),
        help="world date to build (repeatable). Defaults to both.",
    )
    args = parser.parse_args()
    world_dates = args.world_dates or list(WAYBACK_SNAPSHOTS)
    return run(world_dates)


if __name__ == "__main__":
    sys.exit(main())
