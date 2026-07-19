"""Frozen-world depth-chart assembly (docs/phase2-backtest-harness-design.md §2's
"Depth charts, rosters ... as of the world date" component, build-order step 1).

nflverse's own load_depth_charts()/load_rosters_weekly() both start at Week 1
of the regular season (September+) -- after both frozen-world dates, so using
them would either not exist yet or be outright leakage. load_trades() has
dated records but doesn't cover draft picks or free-agent signings, so it
can't reconstruct a full roster alone. Researched the Wayback Machine instead
(same approach as historical_adp.py / D-009): ESPN's and Pro-Football-
Reference's team roster pages both have sparse coverage (23-37 days before
either world date). Ourlads.com's NFL depth-chart pages are much closer and
were confirmed to have been crawled league-wide in the same narrow window:
2024-06-03 (15 days before the 2024-06-18 world) and 2025-06-16/17 (1-2 days
before the 2025-06-18 world) -- verified across multiple teams, and the
archived HTML is a genuine structured depth chart (offense/defense grouped,
5-deep player ordering per position), not a JS shell Wayback failed to
capture.

Unlike the ADP source, Ourlads has no shared player id with the nflverse/
DynastyProcess crosswalk -- resolution is name-based PROPOSALS ONLY (same
discipline as capture/crosswalk.py's FFC handling): confident matches go into
the output table, ambiguous/unmatched ones go into a review queue and are
NEVER auto-confirmed.

Scope: offense skill positions only (QB/RB/WR/TE) -- charter §4 excludes
everything else, and this project doesn't need opposing defensive depth.

Usage (either venv -- requests/pandas only, no nflreadpy dependency):
    python -m backtest.frozen_worlds.historical_depth_charts               # both worlds
    python -m backtest.frozen_worlds.historical_depth_charts --world 2024-06-18
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from access.snapshot_resolver import load_curated_table, resolve_snapshot_date
from capture.crosswalk import apply_confirmed_overrides, propose_by_name
from capture.manifest_utils import git_commit, sha256_file

# The same normalized-name collisions Nick already reviewed and confirmed for
# FFC (capture/ffc_confirmed_matches.json, phase1 §3) apply here too -- the
# ambiguity lives in the crosswalk itself (e.g. "Lamar Jackson" collides with
# a same-named CB regardless of which source proposed the match), not in FFC
# specifically. Reusing an EXISTING human confirmation for the same literal
# name is a mechanical extension, not a new judgment call -- this does NOT
# add new entries to that file (its own docstring's "never add
# programmatically" rule is about new collisions, which still require Nick's
# review via a fresh unmatched-queue entry).
FFC_CONFIRMED_MATCHES_PATH = Path("capture/ffc_confirmed_matches.json")

REQUEST_TIMEOUT_S = 30
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}
CDX_API = "https://web.archive.org/cdx/search/cdx"
OURLADS_BASE = "https://www.ourlads.com/nfldepthcharts/depthchart"
# Delay between the 32 CDX lookups + 32 page fetches per world. archive.org's
# free public endpoints were observed dropping connections outright (not just
# 429s) under this project's request volume even with retries -- a longer,
# politer delay plus per-team fault isolation (below) matter more than speed
# here, since this only needs to run twice total (once per frozen world).
REQUEST_DELAY_S = 3.0

# Project's canonical 32 team abbreviations (matches capture/ convention,
# e.g. LAR not LA -- see CLAUDE.md's Rams team-code note).
CHARTER_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
    "TEN", "WAS",
]
# Ourlads uses a different code only for Arizona (ARZ, not ARI) -- verified
# 2026-07-18 by enumerating every team code Ourlads' site actually used
# across all of 2024's Wayback crawls. Every other team matches this
# project's convention exactly.
OURLADS_TEAM_CODE = {"ARI": "ARZ"}

# charter §4 scope: QB/RB/WR/TE only. Ourlads splits WR into 3 depth slots
# (left/right/slot) and RB sometimes includes FB on the same line -- mapped
# down to the 4 charter positions; FB and O-line/defense/ST rows are dropped.
_POSITION_MAP_SUBSTR = [
    ("QB", "QB"),
    ("WR", "WR"),  # LWR, RWR, SWR all -> WR
    ("TE", "TE"),
    ("RB", "RB"),  # but NOT "FB" -- checked separately below
]

FROZEN_WORLDS_ROOT = Path("backtest/frozen_worlds")


def _get_with_retries(url: str, **kwargs) -> requests.Response:
    """archive.org's free public endpoints occasionally drop a connection
    outright (not just 429s) under this project's request volume (32 teams x
    2 requests each) -- a longer retry-with-backoff avoids failing the whole
    pull over one blip. Caller still handles a persistent failure (below)."""
    last_exc = None
    for attempt in range(6):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT_S, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            time.sleep(min(3 * (2**attempt), 60))
    raise last_exc


def _closest_snapshot_on_or_before(ourlads_code: str, world_date: str) -> dict | None:
    """CDX lookup for the latest Ourlads depth-chart snapshot at or before
    world_date (inclusive of the whole day) -- the leakage-safe pick."""
    to_ts = world_date.replace("-", "") + "235959"
    resp = _get_with_retries(
        CDX_API,
        params={
            "url": f"ourlads.com/nfldepthcharts/depthchart/{ourlads_code}",
            "matchType": "exact",
            "to": to_ts,
            "output": "json",
            "filter": "statuscode:200",
            "limit": -1,  # most recent match at/before `to`
        },
    )
    rows = resp.json()
    if len(rows) < 2:  # rows[0] is the CDX header row
        return None
    _, timestamp, original_url = rows[-1][:3]
    return {"timestamp": timestamp, "original_url": original_url}


def fetch_team_snapshot(team: str, world_date: str) -> tuple[str, dict] | tuple[None, None]:
    ourlads_code = OURLADS_TEAM_CODE.get(team, team)
    snap = _closest_snapshot_on_or_before(ourlads_code, world_date)
    if snap is None:
        return None, None
    url = f"https://web.archive.org/web/{snap['timestamp']}id_/{snap['original_url']}"

    # A 200 status alone isn't proof of real content -- under strain,
    # archive.org has been observed returning short/degraded bodies that
    # still pass raise_for_status() but lack the actual depth-chart table
    # (the root cause of a real bug: 32/32 teams silently parsed 0 rows).
    # Retry the whole fetch (not just the connection) if the marker is missing.
    # NOTE: the marker is 'ctl00_phContent_dcTBody' (Ourlads' own tbody id) --
    # NOT 'id="data"', which is the *ADP page's* table id from
    # historical_adp.py; a copy-paste mix-up here caused every team to be
    # wrongly flagged as failed on 2026-07-18 even though the real table was
    # present the whole time. Verified against a known-good sample page.
    last_html = None
    for attempt in range(3):
        resp = _get_with_retries(url, headers=REQUEST_HEADERS)
        last_html = resp.text
        if "ctl00_phContent_dcTBody" in last_html:
            return last_html, snap
        time.sleep(min(5 * (2**attempt), 30))
    raise requests.exceptions.RequestException(
        f"{team}: fetched {url} 3x but none contained the expected depth-chart table marker "
        f"(last response length: {len(last_html)} chars) -- treating as a failed fetch"
    )


def _clean_player_name(raw: str) -> str | None:
    """Ourlads cells read 'Lastname, Firstname <status code>' (e.g. 'London,
    Drake 22/1', 'Mooney, Darnell U/Atl') -- the status code (draft year/
    round, undrafted-from-team, street/college-free-agent-year) is dropped;
    only the first whitespace token after the comma is the first name."""
    raw = raw.strip()
    if not raw or "," not in raw:
        return None
    last, _, rest = raw.partition(",")
    first = rest.strip().split(" ")[0].strip()
    if not first or not last.strip():
        return None
    return f"{first.strip()} {last.strip()}"


def parse_depth_chart_html(html: str, team: str) -> pd.DataFrame:
    """Ourlads has served at least two different page layouts across the
    Wayback snapshots this project uses: 2024-era pages put offense/defense/
    special-teams as row groups inside ONE <table>, divided by inline
    "Offense"/"Defense" marker rows; 2025-era pages instead split them into
    THREE separate <table> elements with no inline marker at all (verified
    2026-07-18 against a real New England snapshot -- relying on the marker
    silently produced 0 rows for every 2025 team, since a marker that isn't
    there never flips `section` to "Offense"). Fixed by dropping section-
    tracking entirely: every row on the page is scanned regardless of which
    table or divider it falls under, and offense scoping comes purely from
    each row's OWN position label (QB/RB/WR/TE substring match) -- O-line
    (LT/LG/C/RG/RT), defensive (DE/DT/LB/CB/S/...), and special-teams (K/P/
    LS/...) position codes never match those substrings, so this is safe
    without needing to know which table/section a row physically sits in.
    """
    row_blocks = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    records = []
    for block in row_blocks:
        pos_match = re.search(r"<td class='row-dc-\w+'>([A-Z]+)</td>", block)
        if not pos_match:
            continue
        raw_pos = pos_match.group(1)
        if raw_pos == "FB":
            continue  # out of charter scope, and would otherwise collide with the RB substring map
        mapped_pos = next((p for substr, p in _POSITION_MAP_SUBSTR if substr in raw_pos), None)
        if mapped_pos is None:
            continue

        # Table header is "Player 1".."Player 5" (5 depth slots) -- cap at 5
        # in case a row's markup carries a stray extra <a> (observed on some
        # rows) that would otherwise shift/duplicate depth_order downstream.
        player_cells = re.findall(r"<a href='[^']*'[^>]*>([^<]*)</a>", block)[:5]
        for depth_order, raw_name in enumerate(player_cells, start=1):
            name = _clean_player_name(raw_name)
            if name is None:
                continue  # blank slot (team doesn't have a 4th/5th-string player listed)
            records.append(
                {
                    "team": team,
                    "position": mapped_pos,
                    "depth_chart_position_raw": raw_pos,
                    "depth_order": depth_order,
                    "player_name": name,
                }
            )
    return pd.DataFrame.from_records(records)


def build_frozen_world_depth_chart(world_date: str, crosswalk_snapshot_date: str | None = None, teams: list[str] | None = None):
    teams = teams or CHARTER_TEAMS
    pinned_crosswalk_date = resolve_snapshot_date(crosswalk_snapshot_date)
    crosswalk = load_curated_table(pinned_crosswalk_date, "nflverse_crosswalk")

    all_rows = []
    team_snapshots = {}
    failures = []
    for team in teams:
        try:
            html, snap = fetch_team_snapshot(team, world_date)
        except requests.exceptions.RequestException as exc:
            print(f"[historical_depth_charts] {world_date}/{team}: request failed after retries -- {exc}")
            failures.append(team)
            time.sleep(REQUEST_DELAY_S)
            continue
        time.sleep(REQUEST_DELAY_S)
        if html is None:
            failures.append(team)
            continue
        team_df = parse_depth_chart_html(html, team)
        if len(team_df) == 0:
            # HTML fetched (200 OK) but zero offense rows parsed -- almost
            # certainly a degraded/placeholder page served under archive.org
            # strain, not a real 0-player depth chart. A list of empty
            # DataFrames is still a non-empty LIST, so this can't rely on
            # "all_rows is empty" below -- must be caught here, per-team.
            print(f"[historical_depth_charts] {world_date}/{team}: fetched but parsed 0 offense rows -- treating as a failure")
            failures.append(team)
            continue
        all_rows.append(team_df)
        team_snapshots[team] = snap["timestamp"]

    total_rows = sum(len(df) for df in all_rows)
    if total_rows == 0:
        raise RuntimeError(
            f"{world_date}: 0/{len(teams)} teams produced usable rows (failed: {failures}) -- "
            "likely a sustained archive.org rate-limit/connection block rather than a real per-team gap. "
            "Not writing an empty/misleading output; wait and retry."
        )
    combined = pd.concat(all_rows, ignore_index=True)
    proposed, unmatched, stats = propose_by_name(combined, "player_name", crosswalk, crosswalk_name_col="merge_name")

    overrides = json.loads(FFC_CONFIRMED_MATCHES_PATH.read_text())["confirmed"] if FFC_CONFIRMED_MATCHES_PATH.exists() else []
    valid_gsis_ids = set(crosswalk["gsis_id"].dropna())
    confirmed, unmatched, override_warnings = apply_confirmed_overrides(unmatched, "player_name", overrides, valid_gsis_ids)
    for w in override_warnings:
        print(f"[historical_depth_charts] {world_date}: {w}")
    if len(confirmed):
        proposed = pd.concat([proposed, confirmed], ignore_index=True)
        stats = {**stats, "confirmed_via_ffc_overrides": len(confirmed), "proposed_rows": stats["proposed_rows"] + len(confirmed),
                 "unmatched_rows": stats["unmatched_rows"] - len(confirmed)}

    return proposed, unmatched, stats, team_snapshots, failures, pinned_crosswalk_date


def write_frozen_world_depth_chart(
    world_date: str, crosswalk_snapshot_date: str | None = None, teams: list[str] | None = None, merge: bool = False
) -> Path:
    """merge=True: backfill only `teams` (e.g. the couple that failed a full
    run) and union the result into the existing output rather than
    overwriting it -- avoids re-fetching all 32 teams just to fix 1-2."""
    proposed, unmatched, stats, team_snapshots, failures, pinned_crosswalk_date = build_frozen_world_depth_chart(
        world_date, crosswalk_snapshot_date, teams=teams
    )

    out_dir = FROZEN_WORLDS_ROOT / world_date / "raw" / "ourlads_depth_chart"
    out_dir.mkdir(parents=True, exist_ok=True)
    proposed_path = out_dir / "depth_chart_proposed.parquet"
    unmatched_path = out_dir / "unmatched_queue.parquet"

    prior_team_snapshots = {}
    if merge and proposed_path.exists():
        fetched_teams = set(team_snapshots)
        prior_manifest = json.loads((out_dir / "manifest.json").read_text())
        prior_team_snapshots = {
            t: ts for t, ts in prior_manifest["source"]["per_team_source_timestamp"].items() if t not in fetched_teams
        }
        prior_proposed = pd.read_parquet(proposed_path)
        prior_unmatched = pd.read_parquet(unmatched_path)
        proposed = pd.concat([prior_proposed[~prior_proposed["team"].isin(fetched_teams)], proposed], ignore_index=True)
        unmatched = pd.concat([prior_unmatched[~prior_unmatched["team"].isin(fetched_teams)], unmatched], ignore_index=True)
        failures = [f for f in prior_manifest["source"]["teams_failed"] if f not in fetched_teams] + [
            f for f in failures if f not in prior_team_snapshots
        ]
        stats = {
            **stats,
            "total_rows": len(proposed) + len(unmatched),
            "proposed_rows": len(proposed),
            "unmatched_rows": len(unmatched),
            "proposed_pct": round(100.0 * len(proposed) / (len(proposed) + len(unmatched)), 2) if (len(proposed) + len(unmatched)) else 0.0,
        }

    team_snapshots = {**prior_team_snapshots, **team_snapshots}
    proposed.to_parquet(proposed_path, index=False)
    unmatched.to_parquet(unmatched_path, index=False)

    latest_source_timestamp = max(team_snapshots.values()) if team_snapshots else None
    manifest = {
        "world_date": world_date,
        "generated_at": datetime.now().isoformat(),
        "code_git_commit": git_commit(),
        "source": {
            "provider": "Ourlads NFL depth charts (ourlads.com/nfldepthcharts), via Wayback Machine archive",
            "teams_requested": len(CHARTER_TEAMS),
            "teams_fetched": len(team_snapshots),
            "teams_failed": failures,
            "per_team_source_timestamp": team_snapshots,
            "latest_source_timestamp": latest_source_timestamp,
        },
        "resolution": {
            "method": "PROPOSED name match (propose_by_name) plus reuse of capture/ffc_confirmed_matches.json's "
            "EXISTING human-confirmed name collisions (same crosswalk ambiguity, not FFC-specific -- see code "
            "comment) -- Ourlads has no shared id with the crosswalk, unlike the ADP source's deterministic fp_id "
            "join. Anything still in the unmatched queue below is a genuinely new collision/gap requiring fresh "
            "human review before being trusted, same discipline as "
            "capture/ffc_confirmed_matches.json.",
            "crosswalk_snapshot_date": pinned_crosswalk_date,
            **stats,
        },
        "scope": "offense skill positions only (QB/RB/WR/TE) per charter.md §4 -- O-line/defense/special-teams/FB rows dropped",
        "files": {
            "proposed_matches": {"path": str(proposed_path), "sha256": sha256_file(proposed_path), "row_count": len(proposed)},
            "unmatched_queue": {"path": str(unmatched_path), "sha256": sha256_file(unmatched_path), "row_count": len(unmatched)},
        },
        "leakage_audit": {
            "source_timestamp_on_or_before_world_date": True,
            "note": f"every team's snapshot was queried with an explicit CDX `to={world_date}` cutoff, so each "
            "one is individually guaranteed <= world_date by construction, not just the latest of the batch.",
        },
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(
        f"[historical_depth_charts] {world_date}: {len(team_snapshots)}/{len(CHARTER_TEAMS)} teams fetched "
        f"(failed: {failures or 'none'}), {stats['proposed_rows']} proposed / {stats['unmatched_rows']} unmatched "
        f"-- wrote {proposed_path}, {unmatched_path}, {manifest_path}"
    )
    return proposed_path


def run(world_dates: list[str], teams: list[str] | None = None, merge: bool = False) -> int:
    for world_date in world_dates:
        write_frozen_world_depth_chart(world_date, teams=teams, merge=merge)
    return 0


def main() -> int:
    from backtest.frozen_worlds.historical_adp import WAYBACK_SNAPSHOTS  # world-date registry, shared across sources

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--world", action="append", dest="world_dates", choices=list(WAYBACK_SNAPSHOTS),
        help="world date to build (repeatable). Defaults to both.",
    )
    parser.add_argument(
        "--team", action="append", dest="teams", choices=CHARTER_TEAMS,
        help="backfill only this team (repeatable) instead of all 32 -- e.g. after a full run left 1-2 teams "
        "failed. Implies --merge.",
    )
    parser.add_argument(
        "--merge", action="store_true",
        help="union new results into the existing output instead of overwriting it (implied by --team).",
    )
    args = parser.parse_args()
    world_dates = args.world_dates or list(WAYBACK_SNAPSHOTS)
    return run(world_dates, teams=args.teams, merge=args.merge or bool(args.teams))


if __name__ == "__main__":
    sys.exit(main())
