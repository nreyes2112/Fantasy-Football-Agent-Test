"""ESPN unofficial API: league settings (source of truth, diffed against
charter.md §5) and this league's own historical draft results (used for the
DELTA/My Guys pricing once this becomes the primary ADP source per D-005).

Unofficial and undocumented -- endpoint shape and field names verified
against the ESPN v3 API as reverse-engineered by the community (espn-api
project, github.com/cwendt94/espn-api) rather than any ESPN documentation.
Private leagues require the owner's SWID and espn_s2 session cookies
(charter.md §9); these are read from environment variables, never hardcoded
or committed (see .env, gitignored).
"""

from __future__ import annotations

import json

import pandas as pd
import requests

# fantasy.espn.com now sits behind an AWS WAF challenge that a plain HTTP
# client can't solve (verified 2026-07-18: x-amzn-waf-action: challenge,
# empty 202 body). lm-api-reads.fantasy.espn.com is ESPN's dedicated
# programmatic-read host and returns real data with the same view params.
BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"
REQUEST_TIMEOUT_S = 30
# A browser-like User-Agent avoids a second WAF surface seen on the
# fantasy.espn.com host; kept here defensively even though lm-api-reads
# hasn't required it in testing.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# ESPN roster slotId -> position label, matching charter.md §5's vocabulary.
# Unmapped slot ids are surfaced as "SLOT_<id>" rather than silently dropped.
POSITION_SLOT_MAP = {
    0: "QB",
    2: "RB",
    4: "WR",
    6: "TE",
    16: "D/ST",
    17: "K",
    20: "BE",
    21: "IR",
    23: "RB/WR/TE",  # FLEX
}

# ESPN scoring statId -> what charter.md §5 calls it.
STAT_ID_PASSING_TD = 4
STAT_ID_RECEPTION = 53

# ESPN player.defaultPositionId -> position label (distinct numbering from
# roster slot ids above). Verified against this league's live player pool
# 2026-07-18: 1=QB (Josh Allen), 2=RB (Jahmyr Gibbs), 3=WR (Puka Nacua),
# 4=TE (Trey McBride), 5=K (Brandon Aubrey), 16=D/ST (Broncos D/ST).
POSITION_ID_MAP = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

DEFAULT_PLAYER_POOL_LIMIT = 600  # comfortably covers charter's QB24/RB48/WR60/TE24 universe


class EspnAuthError(RuntimeError):
    """Raised on 401/403 -- cookies are missing, wrong, or expired."""


def fetch_league_settings(season: int, league_id: int, swid: str, espn_s2: str) -> dict:
    url = f"{BASE_URL}/{season}/segments/0/leagues/{league_id}"
    resp = requests.get(
        url,
        params={"view": "mSettings"},
        cookies={"SWID": swid, "espn_s2": espn_s2},
        headers=REQUEST_HEADERS,
        timeout=REQUEST_TIMEOUT_S,
    )
    if resp.status_code in (401, 403):
        raise EspnAuthError(
            f"ESPN returned {resp.status_code} -- SWID/espn_s2 cookies are missing, "
            "wrong, or expired. Re-extract them from a logged-in browser session."
        )
    resp.raise_for_status()
    data = resp.json()
    if "settings" not in data:
        raise ValueError(f"ESPN mSettings response missing 'settings' key: {list(data.keys())!r}")
    return data


def summarize_settings(raw: dict) -> dict:
    """Pull out exactly the charter-relevant fields, leaving everything else in `raw`."""
    settings = raw["settings"]

    roster_slots = {}
    for slot_id_str, count in settings.get("rosterSettings", {}).get("lineupSlotCounts", {}).items():
        slot_id = int(slot_id_str)
        if count == 0:
            continue
        label = POSITION_SLOT_MAP.get(slot_id, f"SLOT_{slot_id}")
        roster_slots[label] = count

    scoring_by_stat_id = {
        item["statId"]: item.get("points")
        for item in settings.get("scoringSettings", {}).get("scoringItems", [])
    }

    return {
        "league_id": raw.get("id"),
        "season_id": raw.get("seasonId"),
        "league_name": settings.get("name"),
        "team_count": settings.get("size"),
        "roster_slots": roster_slots,
        "passing_td_points": scoring_by_stat_id.get(STAT_ID_PASSING_TD),
        "reception_points": scoring_by_stat_id.get(STAT_ID_RECEPTION),
        "scoring_type": settings.get("scoringSettings", {}).get("scoringType"),
        "playoff_team_count": settings.get("scheduleSettings", {}).get("playoffTeamCount"),
    }


def diff_against_charter(summary: dict, expected_roster: dict, expected_teams: int,
                          expected_passing_td_points: float, expected_reception_points: float) -> list[dict]:
    """Charter §5 vs. what ESPN actually reports. Every row is reported --
    matches and mismatches alike -- so a passing run is auditable, not just
    a failing one."""
    checks = []

    checks.append({
        "field": "team_count",
        "expected": expected_teams,
        "actual": summary["team_count"],
        "match": summary["team_count"] == expected_teams,
    })
    checks.append({
        "field": "passing_td_points",
        "expected": expected_passing_td_points,
        "actual": summary["passing_td_points"],
        "match": summary["passing_td_points"] == expected_passing_td_points,
    })
    checks.append({
        "field": "reception_points (PPR)",
        "expected": expected_reception_points,
        "actual": summary["reception_points"],
        "match": summary["reception_points"] == expected_reception_points,
    })

    all_slot_labels = set(expected_roster) | set(summary["roster_slots"])
    for label in sorted(all_slot_labels):
        expected_count = expected_roster.get(label, 0)
        actual_count = summary["roster_slots"].get(label, 0)
        checks.append({
            "field": f"roster_slot[{label}]",
            "expected": expected_count,
            "actual": actual_count,
            "match": expected_count == actual_count,
        })

    return checks


def fetch_player_pool(
    season: int, league_id: int, swid: str, espn_s2: str, limit: int = DEFAULT_PLAYER_POOL_LIMIT
) -> list[dict]:
    """This league's live ADP/ownership view (kona_player_info), sorted by
    ESPN's own STANDARD draft rank. This is ESPN's per-league ADP -- distinct
    from FantasyFootballCalculator's cross-site aggregate (D-007) -- and per
    D-005 is meant to become the primary ADP source for the board's DELTA
    column and My Guys pricing.
    """
    url = f"{BASE_URL}/{season}/segments/0/leagues/{league_id}"
    headers = dict(REQUEST_HEADERS)
    headers["x-fantasy-filter"] = json.dumps(
        {
            "players": {
                "limit": limit,
                "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
            }
        }
    )
    resp = requests.get(
        url,
        params={"view": "kona_player_info"},
        cookies={"SWID": swid, "espn_s2": espn_s2},
        headers=headers,
        timeout=REQUEST_TIMEOUT_S,
    )
    if resp.status_code in (401, 403):
        raise EspnAuthError(
            f"ESPN returned {resp.status_code} on kona_player_info -- SWID/espn_s2 cookies "
            "are missing, wrong, or expired."
        )
    resp.raise_for_status()
    data = resp.json()
    if "players" not in data:
        raise ValueError(f"ESPN kona_player_info response missing 'players' key: {list(data.keys())!r}")
    return data["players"]


def player_pool_to_dataframe(players: list[dict]) -> pd.DataFrame:
    rows = []
    for entry in players:
        p = entry["player"]
        ownership = p.get("ownership", {})
        rows.append(
            {
                "player_id": p.get("id"),
                "full_name": p.get("fullName"),
                "position": POSITION_ID_MAP.get(p.get("defaultPositionId"), f"POS_{p.get('defaultPositionId')}"),
                "pro_team_id": p.get("proTeamId"),
                "injury_status": p.get("injuryStatus"),
                "average_draft_position": ownership.get("averageDraftPosition"),
                "average_draft_position_pct_change": ownership.get("averageDraftPositionPercentChange"),
                "percent_owned": ownership.get("percentOwned"),
                "percent_started": ownership.get("percentStarted"),
                "auction_value_average": ownership.get("auctionValueAverage"),
            }
        )
    return pd.DataFrame(rows)
