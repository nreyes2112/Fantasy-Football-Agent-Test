"""Sleeper API pulls: player meta/injury status and trending adds/drops.

Sleeper's public API (https://docs.sleeper.com/) has no ADP endpoint -- despite
phase1-data-platform-design.md §2 listing "ADP" under Sleeper, the only
documented endpoints are the full player list and trending adds/drops.
ADP is pulled from FantasyFootballCalculator instead (see ffc_adp.py).
See decisions.md D-007.
"""

from __future__ import annotations

import json

import pandas as pd
import requests

BASE_URL = "https://api.sleeper.app/v1"
REQUEST_TIMEOUT_S = 30

# Sleeper asks that /players/nfl be called at most once per day -- fine, since
# this job itself only runs once per day.
PLAYERS_URL = f"{BASE_URL}/players/nfl"
TRENDING_URL_TEMPLATE = f"{BASE_URL}/players/nfl/trending/{{direction}}"


def fetch_players() -> dict:
    """Full Sleeper player dict, keyed by sleeper player_id."""
    resp = requests.get(PLAYERS_URL, timeout=REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data:
        raise ValueError("Sleeper /players/nfl returned an empty or unexpected payload")
    return data


def fetch_trending(direction: str, lookback_hours: int = 24, limit: int = 250) -> list[dict]:
    """Trending adds or drops. direction must be 'add' or 'drop'."""
    if direction not in ("add", "drop"):
        raise ValueError(f"direction must be 'add' or 'drop', got {direction!r}")
    url = TRENDING_URL_TEMPLATE.format(direction=direction)
    resp = requests.get(
        url,
        params={"lookback_hours": lookback_hours, "limit": limit},
        timeout=REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Sleeper trending/{direction} returned an unexpected payload")
    return data


def _stringify_nested(value):
    """JSON-encode dict/list fields so every column is parquet-safe scalar data."""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def players_to_dataframe(players: dict) -> pd.DataFrame:
    """One row per player, every field Sleeper returned kept as-pulled.

    Nested fields (e.g. `metadata`, `competitions`) are JSON-encoded to strings
    so the raw file stays a flat, parquet-safe table without dropping data.
    """
    rows = []
    for player_id, fields in players.items():
        row = {k: _stringify_nested(v) for k, v in fields.items()}
        row["player_id"] = player_id
        rows.append(row)
    df = pd.DataFrame(rows)
    return df


def trending_to_dataframe(trending: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(trending)
