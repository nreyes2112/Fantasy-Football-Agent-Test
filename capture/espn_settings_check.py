"""One-off/periodic check: pull the real ESPN league settings and diff them
against charter.md §5 (phase1-data-platform-design.md §2's "closing check").

This is NOT part of the daily Tier 1 cadence and does not touch
data/snapshots/ or its manifest gating -- it's a standalone audit you re-run
whenever you want to confirm the charter still matches league reality (e.g.
after the commissioner changes a setting). Output goes to
data/espn_settings_checks/<timestamp>.json, one file per run, never edited.

Usage:
    python -m capture.espn_settings_check
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from capture.config import (
    CHARTER_PASSING_TD_POINTS,
    CHARTER_RECEPTION_POINTS,
    CHARTER_ROSTER_SLOTS,
    ESPN_LEAGUE_ID,
    ESPN_SEASON,
    LEAGUE_TEAMS,
    LEAGUE_TIMEZONE,
)
from capture.sources import espn

CHECK_OUTPUT_DIR = Path("data/espn_settings_checks")


def _require_env(name: str) -> str:
    import os

    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"{name} is not set. Put it in a local .env file (gitignored) or export it -- "
            "see charter.md §9 for how to obtain SWID/espn_s2."
        )
    return value


def run() -> int:
    load_dotenv()
    swid = _require_env("ESPN_SWID")
    espn_s2 = _require_env("ESPN_S2")

    print(f"[espn-check] pulling settings for league {ESPN_LEAGUE_ID}, season {ESPN_SEASON}...")
    try:
        raw = espn.fetch_league_settings(ESPN_SEASON, ESPN_LEAGUE_ID, swid, espn_s2)
    except espn.EspnAuthError as e:
        print(f"[espn-check] AUTH FAILURE: {e}")
        return 1

    summary = espn.summarize_settings(raw)
    checks = espn.diff_against_charter(
        summary,
        expected_roster=CHARTER_ROSTER_SLOTS,
        expected_teams=LEAGUE_TEAMS,
        expected_passing_td_points=CHARTER_PASSING_TD_POINTS,
        expected_reception_points=CHARTER_RECEPTION_POINTS,
    )
    all_match = all(c["match"] for c in checks)

    print(f"[espn-check] league: {summary['league_name']!r} (id={summary['league_id']}, season={summary['season_id']})")
    print(f"[espn-check] {'ALL CHARTER FIELDS MATCH' if all_match else 'MISMATCH FOUND'}")
    for c in checks:
        status = "OK  " if c["match"] else "DIFF"
        print(f"  [{status}] {c['field']}: expected={c['expected']!r} actual={c['actual']!r}")

    CHECK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(LEAGUE_TIMEZONE).strftime("%Y-%m-%dT%H%M%S")
    out_path = CHECK_OUTPUT_DIR / f"{timestamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "checked_at": datetime.now(LEAGUE_TIMEZONE).isoformat(),
                "summary": summary,
                "checks": checks,
                "all_match": all_match,
                "raw_settings": raw,
            },
            indent=2,
        )
    )
    print(f"[espn-check] wrote {out_path}")
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(run())
