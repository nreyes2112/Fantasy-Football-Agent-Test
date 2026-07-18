"""Agent Access Layer (phase1-data-platform-design.md §7) -- the ONLY path
to numbers for agents (Phase 3+). Every function is read-only, pinned to
one GOLD snapshot per call (see snapshot_resolver.py), and returns a
citation payload: {values, source, snapshot_date, schema_version,
available, note}.

Player identity everywhere here is the canonical nflverse `gsis_id` (§3) --
never a Sleeper/ESPN/FFC platform id.

get_player_stats and get_team_context now read real nflverse data
(player_stats/team_stats, 2024-2025 seasons, capture/pull_stats.py).
get_vacated_opportunity still needs season-over-season nflverse pbp/roster
data to identify departed players, not ingested yet -- it honestly reports
`available: False` with a `note` explaining the gap, per the project's
no-fabrication rule, rather than fabricate or get skipped entirely. Some
individual fields within get_team_context (PROE, Vegas win total, OL rank)
are also genuinely unavailable even with team_stats pulled -- see that
function's docstring for why each one specifically can't be answered yet.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from access.snapshot_resolver import (
    gold_snapshot_dates,
    load_curated_table,
    load_raw_table,
    resolve_snapshot_date,
    schema_version,
)
from capture.config import ESPN_LEAGUE_ID, ESPN_SEASON

NOT_YET_INGESTED_NOTE = (
    "nflverse pbp/roster history has not been ingested yet (Phase 1 §2 -- only "
    "load_ff_playerids(), load_player_stats(), and load_team_stats() have been pulled so "
    "far). This is not a missing team; it's a source this project hasn't built yet."
)

# Rate/share stats are averaged across a window; everything else (raw
# counting stats, and EPA -- reported per-game as a total, so summed like
# any other counting stat) is summed. This is a convention, documented here
# rather than left implicit.
_RATE_METRICS = {"target_share", "air_yards_share", "wopr", "racr", "passing_cpoe", "pacr", "fg_pct", "pat_pct"}


def _response(values, source: str, snapshot_date: str | None, schema_version_: str | None,
              available: bool = True, note: str | None = None) -> dict:
    return {
        "values": values,
        "source": source,
        "snapshot_date": snapshot_date,
        "schema_version": schema_version_,
        "available": available,
        "note": note,
    }


def _unavailable(source: str, note: str) -> dict:
    return _response(None, source, None, None, available=False, note=note)


# --- get_player_stats -------------------------------------------------

_SUPPORTED_WINDOWS = ("season", "last4", "last8")


def get_player_stats(player_id: str, metrics: list[str], window: str, snapshot_date: str | None = None) -> dict:
    """player_id = gsis_id. metrics = e.g. ['target_share', 'receiving_epa'].
    window = 'season' (most recent season with data), 'last4', or 'last8'
    (most recent N games by season+week -- 'post_event' windows from phase1
    §5's data dictionary aren't supported yet, that needs role-change
    detection this project hasn't built).
    """
    if window not in _SUPPORTED_WINDOWS:
        return _unavailable(
            "nflverse player_stats",
            f"window={window!r} not supported yet (supported: {_SUPPORTED_WINDOWS})",
        )

    pinned_date = resolve_snapshot_date(snapshot_date)
    try:
        df = load_raw_table(pinned_date, "nflverse", "player_stats")
    except FileNotFoundError:
        return _unavailable(
            "nflverse player_stats",
            f"no player_stats table in {pinned_date}'s snapshot -- run `python -m capture.pull_stats` (.venv311)",
        )
    player_rows = df[df["player_id"] == player_id].sort_values(["season", "week"])
    if len(player_rows) == 0:
        return _unavailable("nflverse player_stats", f"gsis_id {player_id} not found in player_stats")

    if window == "season":
        latest_season = int(player_rows["season"].max())
        window_rows = player_rows[player_rows["season"] == latest_season]
    else:
        n = int(window.replace("last", ""))
        window_rows = player_rows.tail(n)

    values = {}
    unavailable_metrics = []
    for metric in metrics:
        if metric not in window_rows.columns:
            unavailable_metrics.append(metric)
            continue
        agg = "mean" if metric in _RATE_METRICS else "sum"
        val = window_rows[metric].agg(agg)
        values[metric] = None if pd.isna(val) else round(float(val), 4)

    note = None
    if len(window_rows) < (4 if window == "last4" else 8 if window == "last8" else 0):
        note = f"only {len(window_rows)} game(s) available for window={window!r}, fewer than requested"
    if unavailable_metrics:
        extra = f"metrics not found in player_stats: {unavailable_metrics}"
        note = f"{note}; {extra}" if note else extra

    return _response(
        {"metrics": values, "games_in_window": len(window_rows), "aggregation": "mean for rate stats, sum otherwise"},
        "nflverse player_stats (load_player_stats, week-level)",
        pinned_date,
        schema_version("nflverse_player_stats"),
        note=note,
    )


# --- get_adp ------------------------------------------------------------

def get_adp(player_id: str, history_days: int = 30, snapshot_date: str | None = None) -> dict:
    """ADP history from all sources, over however many days of GOLD-snapshot
    history actually exist -- if fewer than `history_days` days of snapshots
    exist yet, that's reported honestly rather than padded or extrapolated.
    """
    pinned_date = resolve_snapshot_date(snapshot_date)
    all_dates = gold_snapshot_dates()
    cutoff = datetime.strptime(pinned_date, "%Y-%m-%d") - timedelta(days=history_days)
    in_window = [d for d in all_dates if datetime.strptime(d, "%Y-%m-%d") >= cutoff and d <= pinned_date]

    history = []
    for date in in_window:
        row = {"snapshot_date": date}
        try:
            espn_df = load_curated_table(date, "espn_resolved")
            espn_row = espn_df[espn_df["gsis_id"] == player_id]
            if len(espn_row):
                row["espn_adp"] = float(espn_row.iloc[0]["average_draft_position"])
        except FileNotFoundError:
            pass
        try:
            ffc_df = load_curated_table(date, "ffc_proposed_matches")
            ffc_row = ffc_df[ffc_df["gsis_id"] == player_id]
            if len(ffc_row):
                row["ffc_adp"] = float(ffc_row.iloc[0]["adp"])
        except FileNotFoundError:
            pass
        if len(row) > 1:  # more than just snapshot_date -- found in at least one source
            history.append(row)

    if not history:
        return _unavailable(
            "ESPN (primary, D-005) + FantasyFootballCalculator (secondary, D-007)",
            f"gsis_id {player_id} not found in ADP data for any of {len(in_window)} available snapshot date(s)",
        )

    return _response(
        {"history": history, "days_requested": history_days, "days_available": len(in_window)},
        "ESPN (primary, D-005) + FantasyFootballCalculator (secondary, D-007)",
        pinned_date,
        schema_version("espn_resolved"),
        note=None if len(in_window) >= history_days else
        f"only {len(in_window)} snapshot day(s) exist yet, fewer than the {history_days} requested",
    )


# --- get_team_context -----------------------------------------------------

def get_team_context(team: str, season: int, snapshot_date: str | None = None) -> dict:
    """Plays/game and pass rate come from real nflverse team_stats. PROE
    (pass rate OVER EXPECTED -- needs a play-calling model conditioned on
    score/time/down-distance, not just raw pass rate) and Vegas win total
    (a betting-market product nflverse doesn't carry, and D-006 rules out
    a paid odds API) and OL rank (not a raw stat -- that's a paid analyst
    ranking like PFF's) are reported as genuinely unavailable per field,
    not guessed or approximated with something that looks similar but isn't.
    """
    pinned_date = resolve_snapshot_date(snapshot_date)
    try:
        df = load_raw_table(pinned_date, "nflverse", "team_stats")
    except FileNotFoundError:
        return _unavailable(
            "nflverse team_stats",
            f"no team_stats table in {pinned_date}'s snapshot -- run `python -m capture.pull_stats` (.venv311)",
        )

    team_rows = df[(df["team"] == team) & (df["season"] == season)]
    if len(team_rows) == 0:
        return _unavailable("nflverse team_stats", f"no rows for team={team!r} season={season} in {pinned_date}'s snapshot")

    total_plays = team_rows["attempts"] + team_rows["carries"]
    values = {
        "games": len(team_rows),
        "plays_per_game": round(float(total_plays.mean()), 2),
        "pass_rate": round(float(team_rows["attempts"].sum() / total_plays.sum()), 4),
        "passing_epa_per_game": round(float(team_rows["passing_epa"].mean()), 4),
        "rushing_epa_per_game": round(float(team_rows["rushing_epa"].mean()), 4),
        "proe": None,       # unavailable -- needs a play-calling expectation model, not just raw pass rate
        "win_total": None,  # unavailable -- Vegas season win-total futures aren't in any free source found (D-006)
        "ol_rank": None,    # unavailable -- not a raw stat; would need a paid analyst ranking (e.g. PFF)
    }
    return _response(
        values,
        "nflverse team_stats (load_team_stats, week-level)",
        pinned_date,
        schema_version("nflverse_team_stats"),
        note="proe/win_total/ol_rank are genuinely unavailable (see docstring), not zero or estimated",
    )


# --- get_depth_chart -------------------------------------------------------

def get_depth_chart(team: str, snapshot_date: str | None = None) -> dict:
    """Sleeper's raw player table carries depth_chart_position/_order per
    player -- usable directly, no separate nflverse depth-chart pull needed."""
    pinned_date = resolve_snapshot_date(snapshot_date)
    sleeper_resolved = load_curated_table(pinned_date, "sleeper_resolved")

    team_rows = sleeper_resolved[sleeper_resolved["team"] == team].copy()
    team_rows = team_rows[team_rows["depth_chart_position"].notna()]
    if len(team_rows) == 0:
        return _unavailable(
            "Sleeper depth_chart_position/depth_chart_order fields",
            f"no depth chart rows found for team={team!r} in {pinned_date}'s snapshot",
        )

    team_rows["depth_chart_order"] = pd.to_numeric(team_rows["depth_chart_order"], errors="coerce")
    team_rows = team_rows.sort_values(["depth_chart_position", "depth_chart_order"])
    entries = [
        {
            "gsis_id": r["gsis_id"],
            "full_name": r["full_name"],
            "depth_chart_position": r["depth_chart_position"],
            "depth_chart_order": None if pd.isna(r["depth_chart_order"]) else int(r["depth_chart_order"]),
            "injury_status": r["injury_status"],
        }
        for _, r in team_rows.iterrows()
    ]
    return _response(entries, "Sleeper (player meta)", pinned_date, schema_version("sleeper_resolved"))


# --- get_vacated_opportunity -----------------------------------------------

def get_vacated_opportunity(team: str) -> dict:
    """Targets/carries vacated by departed players -- needs season-over-season
    nflverse player_stats, not yet ingested."""
    return _unavailable("nflverse player_stats (not yet ingested)", NOT_YET_INGESTED_NOTE)


# --- get_comps --------------------------------------------------------

_COMP_FEATURES = ["age", "draft_round", "draft_pick", "height", "weight"]


def get_comps(player_id: str, features: list[str] | None = None, k: int = 5, snapshot_date: str | None = None) -> dict:
    """Nearest-neighbor comps on bio/draft-capital fields only (age, draft
    round/pick, height, weight) -- the crosswalk doesn't carry efficiency or
    production stats yet, so this is NOT what phase3's profile_analyst will
    eventually need (route participation, YPRR, athletic testing percentiles).
    It's an honest subset, clearly scoped, not a placeholder that pretends
    to be the real thing.
    """
    features = features or _COMP_FEATURES
    unknown = set(features) - set(_COMP_FEATURES)
    if unknown:
        return _unavailable(
            "nflverse crosswalk bio fields",
            f"unsupported features {sorted(unknown)} -- only {_COMP_FEATURES} are available pre-stats-ingestion",
        )

    pinned_date = resolve_snapshot_date(snapshot_date)
    crosswalk = load_curated_table(pinned_date, "nflverse_crosswalk")

    target_rows = crosswalk[crosswalk["gsis_id"] == player_id]
    if len(target_rows) == 0:
        return _unavailable("nflverse crosswalk", f"gsis_id {player_id} not found in {pinned_date}'s crosswalk")
    target = target_rows.iloc[0]

    pool = crosswalk[(crosswalk["position"] == target["position"]) & (crosswalk["gsis_id"] != player_id)].copy()
    pool = pool.dropna(subset=features)
    if len(pool) == 0:
        return _unavailable("nflverse crosswalk", f"no other {target['position']}s with complete {features} data")

    # simple min-max normalized Euclidean distance
    dists = pd.Series(0.0, index=pool.index)
    for feat in features:
        col = pool[feat].astype(float)
        span = col.max() - col.min()
        norm = (col - col.min()) / span if span > 0 else col * 0
        target_val = target[feat]
        target_norm = (target_val - col.min()) / span if span > 0 else 0
        dists += (norm - target_norm) ** 2
    pool = pool.assign(_distance=dists**0.5).sort_values("_distance")

    comps = [
        {"gsis_id": r["gsis_id"], "name": r["name"], "distance": round(float(r["_distance"]), 4),
         **{f: r[f] for f in features}}
        for _, r in pool.head(k).iterrows()
    ]
    return _response(
        {"target": target["name"], "features_used": features, "comps": comps},
        "nflverse crosswalk (bio/draft-capital fields only)",
        pinned_date,
        schema_version("nflverse_crosswalk"),
        note="limited to bio/draft-capital similarity -- no production/efficiency stats ingested yet",
    )


# --- get_league_scoring -------------------------------------------------

def get_league_scoring() -> dict:
    """The Charter scoring function (charter.md §5), cross-verified against
    ESPN's own live settings (capture/espn_settings_check.py) rather than
    re-derived per call -- league settings change rarely and are checked on
    their own cadence, not the daily/weekly snapshot cycle."""
    check_dir = Path("data/espn_settings_checks")
    checks = sorted(check_dir.glob("*.json")) if check_dir.exists() else []
    if not checks:
        return _unavailable(
            "charter.md §5",
            "no ESPN settings-check has been run yet to cross-verify -- run `python -m capture.espn_settings_check`",
        )
    latest_check = json.loads(checks[-1].read_text())
    if not latest_check.get("all_match"):
        return _response(
            latest_check["summary"],
            f"ESPN league {ESPN_LEAGUE_ID} settings, season {ESPN_SEASON}",
            None,
            None,
            available=True,
            note=f"WARNING: last settings-diff ({checks[-1].stem}) found a mismatch vs charter.md §5 -- see {checks[-1]}",
        )
    return _response(
        {
            "team_count": latest_check["summary"]["team_count"],
            "roster_slots": latest_check["summary"]["roster_slots"],
            "scoring_settings": latest_check["raw_settings"]["settings"]["scoringSettings"],
        },
        f"charter.md §5, cross-verified against ESPN league {ESPN_LEAGUE_ID} settings ({checks[-1].stem})",
        None,
        None,
    )


# --- list_data_gaps ----------------------------------------------------

def list_data_gaps(player_id: str, snapshot_date: str | None = None) -> dict:
    """What's missing for this player, so agents can report honestly (§7) --
    checked against every table this project actually produces, not a
    hardcoded list that goes stale."""
    pinned_date = resolve_snapshot_date(snapshot_date)
    gaps = []
    present = []

    for table in ("sleeper_resolved", "espn_resolved", "ffc_proposed_matches", "nflverse_crosswalk"):
        try:
            df = load_curated_table(pinned_date, table)
            if player_id in set(df["gsis_id"].dropna()):
                present.append(table)
            else:
                gaps.append(f"{table}: gsis_id not found/resolved")
        except FileNotFoundError:
            gaps.append(f"{table}: table does not exist in {pinned_date}'s snapshot")

    try:
        stats_df = load_raw_table(pinned_date, "nflverse", "player_stats")
        if player_id in set(stats_df["player_id"].dropna()):
            present.append("nflverse_player_stats")
        else:
            gaps.append("nflverse_player_stats: gsis_id has no rows (never played in 2024-2025, or a rookie)")
    except FileNotFoundError:
        gaps.append(f"nflverse_player_stats: table does not exist in {pinned_date}'s snapshot")

    gaps.append("nflverse pbp/roster history (get_vacated_opportunity): " + NOT_YET_INGESTED_NOTE)

    return _response(
        {"present_in": present, "gaps": gaps},
        "cross-table presence check",
        pinned_date,
        None,
    )
