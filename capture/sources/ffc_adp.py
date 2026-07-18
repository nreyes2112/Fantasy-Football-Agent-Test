"""ADP pulls from FantasyFootballCalculator's free REST API.

Stands in for the ADP data phase1-data-platform-design.md §2 attributes to
"Sleeper API" -- Sleeper's real API doesn't publish ADP (see sleeper.py
docstring and decisions.md D-007). FFC is free, keyless, updates daily, and
is scoped to this league's format via LEAGUE_TEAMS/LEAGUE_SCORING
(config.py, from charter.md §5).

Docs: https://help.fantasyfootballcalculator.com/article/42-adp-rest-api
"""

from __future__ import annotations

import pandas as pd
import requests

BASE_URL = "https://fantasyfootballcalculator.com/api/v1/adp"
REQUEST_TIMEOUT_S = 30


def fetch_adp(scoring: str, teams: int, year: int) -> dict:
    url = f"{BASE_URL}/{scoring}"
    resp = requests.get(url, params={"teams": teams, "year": year}, timeout=REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "Success" or "players" not in payload:
        raise ValueError(f"FFC ADP endpoint returned an unexpected payload: {payload!r}")
    return payload


def adp_to_dataframe(payload: dict) -> pd.DataFrame:
    return pd.DataFrame(payload["players"])


def adp_meta(payload: dict) -> dict:
    """Pull-level metadata (sample size, date window) -- goes in the manifest, not the table."""
    return payload.get("meta", {})
