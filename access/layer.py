"""Agent Access Layer (phase1-data-platform-design.md §7) -- the ONLY path
to numbers for agents (Phase 3+). Every function is read-only, pinned to
one GOLD snapshot per call (see snapshot_resolver.py), and returns a
citation payload: {values, source, snapshot_date, schema_version,
available, note}.

Player identity everywhere here is the canonical nflverse `gsis_id` (§3) --
never a Sleeper/ESPN/FFC platform id.

All 8 functions now return real data for at least their primary case.
get_vacated_opportunity reuses player_stats (season-level team volume) and
sleeper_resolved's live team field (no separate nflverse rosters pull
needed) to detect departed players. Some individual fields still can't be
answered honestly with real data: get_team_context's `proe`/`win_total`/
`ol_rank` are `None` with a reason each (no free source exists for any of
the three) rather than guessed -- see that function's docstring.

FROZEN-WORLD SERVING (phase2 §2's "candidates call the SAME Phase 1 access
layer, just pinned to a frozen snapshot"): when the FROZEN_WORLD_PIN env var
names a world (see snapshot_resolver.pinned_world), every function here
serves as-of-world-date data from backtest/frozen_worlds/ instead --
identical signatures and payload shape, so an agent cannot tell (and never
needs to know) which mode it runs in. Per-function policy: ADP/depth charts
from the world's archived tables; completed-prior-season stats from the
GOLD snapshot hard-filtered to seasons before the world's own; vacated
opportunity re-derived against the archived depth chart (the live version's
"Sleeper current team" field is future information inside a backtest);
comps, injury status, ADP history, and trending honestly unavailable.
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
    load_world_table,
    physical_gold_snapshot_for_world,
    pinned_world,
    resolve_snapshot_date,
    schema_version,
    world_season,
)
from capture.config import ESPN_LEAGUE_ID, ESPN_SEASON

STILL_UNPULLED_SOURCES_NOTE = (
    "nflverse full rosters/depth-charts and draft/combine measurables have not been ingested yet "
    "(Phase 1 §2). Play-by-play (load_pbp) IS pulled as of D-017, but ONLY as an aggregated "
    "red-zone/rush-type summary (rz_targets, rz_carries, designed_carries, scramble_carries, "
    "and their team-level denominators) via capture/pull_pbp.py -- the full 372-column play-level "
    "table is never persisted (repo-size deviation, see that module's docstring), so pbp-derived "
    "metrics beyond that specific summary (success_rate, PROE) remain not yet computable. "
    "get_depth_chart currently substitutes Sleeper's own depth_chart_position/_order fields, "
    "which cover this adequately for now."
)

# Rate/share stats are averaged across a window; everything else (raw
# counting stats, and EPA -- reported per-game as a total, so summed like
# any other counting stat) is summed. This is a convention, documented here
# rather than left implicit.
_RATE_METRICS = {"target_share", "air_yards_share", "wopr", "racr", "passing_cpoe", "pacr", "fg_pct", "pat_pct", "snap_share"}


def _reg_only(df: pd.DataFrame) -> pd.DataFrame:
    """Regular season only, matching backtest/ground_truth.py's explicit
    policy and charter §3's PPG definition -- the fantasy season IS the
    regular season, and mixing postseason rows inflates windows and team
    totals (caught 2026-07-19 by the frozen-world Gibbs cross-check:
    18-game 'season' vs. ground truth's 17 REG games)."""
    if "season_type" not in df.columns:
        return df
    return df[df["season_type"] == "REG"]


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


def _frozen_date_mismatch(world: str, snapshot_date: str | None) -> dict | None:
    """An agent may pass snapshot_date={{SNAPSHOT_DATE}} (= the world date)
    in frozen mode; anything else is a request to read outside the world."""
    if snapshot_date is not None and snapshot_date != world:
        return _unavailable(
            "frozen world",
            f"process is pinned to frozen world {world}; snapshot_date={snapshot_date!r} refused "
            "(pass None or the world date)",
        )
    return None


def _select_window_rows(player_rows: pd.DataFrame, window: str) -> pd.DataFrame:
    if window == "season":
        latest_season = int(player_rows["season"].max())
        return player_rows[player_rows["season"] == latest_season]
    n = int(window.replace("last", ""))
    return player_rows.tail(n)


def _compute_stat_values(
    window_rows: pd.DataFrame, metrics: list[str], load_team_stats, load_team_rz_stats=None
) -> tuple[dict, list[str]]:
    """The single metric-aggregation path shared by live and frozen serving --
    the two modes differ only in which rows reach here, never in how a
    number is computed. `load_team_stats`/`load_team_rz_stats` are zero-arg
    callables (carry_share and the red-zone-share metrics need team totals,
    sourced differently per mode); `load_team_rz_stats` defaults to None
    since most callers never request a red-zone metric."""
    # Local import avoids a circular import (metrics.py imports
    # get_league_scoring from this module).
    from access import metrics as _metrics

    values = {}
    unavailable_metrics = []
    for metric in metrics:
        if metric == "fantasy_points_league_ppg":
            # league-accurate PPG (phase1 §5's flagship requirement -- "PPG",
            # literally points PER GAME, so the window total is divided by
            # games played) -- NOT nflverse's own fantasy_points_ppr, which
            # uses nflverse's own scoring assumptions, not necessarily this
            # league's.
            scoring_by_column = _metrics.league_scoring_by_column()
            if scoring_by_column is None or len(window_rows) == 0:
                unavailable_metrics.append(metric)
                continue
            summed = window_rows.sum(numeric_only=True)
            total_points = _metrics.compute_fantasy_points(summed, scoring_by_column)
            values[metric] = round(total_points / len(window_rows), 2)
        elif metric == "aDOT":
            values[metric] = _metrics.compute_adot(window_rows)
        elif metric == "EPA_per_target":
            values[metric] = _metrics.compute_epa_per_target(window_rows)
        elif metric == "TD_rate":
            values[metric] = _metrics.compute_td_rate(window_rows)
        elif metric == "carry_share":
            try:
                team_stats_df = load_team_stats()
                values[metric] = _metrics.compute_carry_share(window_rows, team_stats_df)
            except FileNotFoundError:
                unavailable_metrics.append(metric)
        elif metric in ("red_zone_target_share", "red_zone_carry_share"):
            if load_team_rz_stats is None or "rz_targets" not in window_rows.columns:
                unavailable_metrics.append(metric)
                continue
            try:
                team_rz_df = load_team_rz_stats()
            except FileNotFoundError:
                unavailable_metrics.append(metric)
                continue
            fn = _metrics.compute_red_zone_target_share if metric == "red_zone_target_share" else _metrics.compute_red_zone_carry_share
            values[metric] = fn(window_rows, team_rz_df)
        elif metric == "designed_run_rate":
            if "designed_carries" not in window_rows.columns:
                unavailable_metrics.append(metric)
                continue
            values[metric] = _metrics.compute_designed_run_rate(window_rows)
        elif metric not in window_rows.columns:
            unavailable_metrics.append(metric)
        else:
            agg = "mean" if metric in _RATE_METRICS else "sum"
            val = window_rows[metric].agg(agg)
            values[metric] = None if pd.isna(val) else round(float(val), 4)
    return values, unavailable_metrics


def _stats_response_values(window_rows: pd.DataFrame, values: dict) -> dict:
    return {
        "metrics": values,
        "games_in_window": len(window_rows),
        "seasons_in_window": sorted(int(s) for s in window_rows["season"].unique()),
        "aggregation": "mean for rate stats, sum otherwise",
    }


def _short_window_note(window_rows: pd.DataFrame, window: str, unavailable_metrics: list[str]) -> str | None:
    note = None
    if len(window_rows) < (4 if window == "last4" else 8 if window == "last8" else 0):
        note = f"only {len(window_rows)} game(s) available for window={window!r}, fewer than requested"
    if unavailable_metrics:
        extra = f"metrics not found in weekly_stats: {unavailable_metrics}"
        note = f"{note}; {extra}" if note else extra
    return note


def _frozen_get_player_stats(world: str, player_id: str, metrics: list[str], window: str) -> dict:
    """Season stats are final, immutable facts about completed seasons, so
    frozen mode serves them from the latest GOLD snapshot's curated table
    hard-filtered to seasons strictly BEFORE the world's own season -- the
    world sits mid-year, before its season is played (D-009). For the
    2024-06-18 world that filter leaves nothing (2023 stats never pulled,
    deferred per Nick 2026-07-19): honestly unavailable, NOT quietly served
    from 2024 -- returning 2024-season stats there would itself be leakage.
    """
    season_cap = world_season(world)
    physical = physical_gold_snapshot_for_world()
    try:
        df = load_curated_table(physical, "weekly_stats")
    except FileNotFoundError:
        return _unavailable(
            "curated/weekly_stats",
            f"no weekly_stats table in {physical}'s snapshot -- run `python -m capture.build_curated_stats` "
            "(after pull_stats and pull_crosswalk, .venv311)",
        )
    df = _reg_only(df)
    df = df[df["season"] < season_cap]
    if len(df) == 0:
        return _unavailable(
            "curated/weekly_stats (frozen-world filtered)",
            f"no pre-{season_cap} season stats exist in the data platform (2023 stats never pulled -- "
            f"deferred per Nick 2026-07-19, D-014), so world {world} has no servable player stats",
        )
    player_rows = df[df["player_id"] == player_id].sort_values(["season", "week"])
    if len(player_rows) == 0:
        return _unavailable(
            "curated/weekly_stats (frozen-world filtered)",
            f"gsis_id {player_id} has no pre-{season_cap} rows (rookie as of {world}, or didn't play)",
        )

    window_rows = _select_window_rows(player_rows, window)

    def load_team_stats():
        team_df = _reg_only(load_raw_table(physical, "nflverse", "team_stats"))
        return team_df[team_df["season"] < season_cap]

    def load_team_rz_stats():
        # redzone_team_stats is curated-only (D-017) and already REG-only
        # by construction (pull_pbp.py filters at aggregation time), but
        # _reg_only() is applied anyway for defense-in-depth consistency
        # with every other stats loader in this module.
        rz_df = _reg_only(load_curated_table(physical, "redzone_team_stats"))
        return rz_df[rz_df["season"] < season_cap]

    values, unavailable_metrics = _compute_stat_values(window_rows, metrics, load_team_stats, load_team_rz_stats)
    return _response(
        _stats_response_values(window_rows, values),
        f"curated/weekly_stats filtered to seasons < {season_cap} (frozen world {world}; "
        f"physical table from GOLD snapshot {physical} -- completed-season facts, identical whenever captured)",
        world,
        schema_version("weekly_stats"),
        note=_short_window_note(window_rows, window, unavailable_metrics),
    )


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

    world = pinned_world()
    if world is not None:
        return _frozen_date_mismatch(world, snapshot_date) or _frozen_get_player_stats(world, player_id, metrics, window)

    pinned_date = resolve_snapshot_date(snapshot_date)
    try:
        # curated/weekly_stats.parquet (capture/build_curated_stats.py) is
        # player_stats already joined with snap_counts_resolved's
        # snap_share -- the actual curated layer phase1 §5's data dictionary
        # names as several metrics' source_tables, not raw player_stats.
        df = load_curated_table(pinned_date, "weekly_stats")
    except FileNotFoundError:
        return _unavailable(
            "curated/weekly_stats",
            f"no weekly_stats table in {pinned_date}'s snapshot -- run `python -m capture.build_curated_stats` "
            "(after pull_stats and pull_crosswalk, .venv311)",
        )
    df = _reg_only(df)
    player_rows = df[df["player_id"] == player_id].sort_values(["season", "week"])
    if len(player_rows) == 0:
        return _unavailable("curated/weekly_stats", f"gsis_id {player_id} not found in weekly_stats")

    window_rows = _select_window_rows(player_rows, window)
    values, unavailable_metrics = _compute_stat_values(
        window_rows, metrics,
        lambda: _reg_only(load_raw_table(pinned_date, "nflverse", "team_stats")),
        lambda: _reg_only(load_curated_table(pinned_date, "redzone_team_stats")),
    )
    return _response(
        _stats_response_values(window_rows, values),
        "curated/weekly_stats (nflverse player_stats + snap_counts_resolved + redzone_player_stats, week-level)",
        pinned_date,
        schema_version("weekly_stats"),
        note=_short_window_note(window_rows, window, unavailable_metrics),
    )


# --- get_adp ------------------------------------------------------------

def _frozen_get_adp(world: str, player_id: str, history_days: int) -> dict:
    """A frozen world has exactly ONE archived ADP snapshot (the Wayback
    capture, D-009) -- ADP movement/history does not exist there and is
    reported as such, never synthesized."""
    adp_df = load_world_table(world, "fantasypros_adp", "adp")
    row = adp_df[adp_df["gsis_id"] == player_id]
    if len(row) == 0:
        return _unavailable(
            "FantasyPros ADP via Wayback Machine (frozen world, D-009)",
            f"gsis_id {player_id} not in world {world}'s archived ADP table ({len(adp_df)} players)",
        )
    r = row.iloc[0]
    history = [{
        "snapshot_date": world,
        "fantasypros_adp": float(r["adp"]),
        "overall_rank": int(r["rank"]),
        "position_rank": int(r["position_rank_at_snapshot"]),
    }]
    return _response(
        {"history": history, "days_requested": history_days, "days_available": 1},
        "FantasyPros ADP via Wayback Machine (frozen world, D-009)",
        world,
        None,
        note="frozen worlds hold a single archived ADP snapshot -- ADP movement/trend history is "
        "structurally unavailable, not merely missing",
    )


def get_adp(player_id: str, history_days: int = 30, snapshot_date: str | None = None) -> dict:
    """ADP history from all sources, over however many days of GOLD-snapshot
    history actually exist -- if fewer than `history_days` days of snapshots
    exist yet, that's reported honestly rather than padded or extrapolated.
    """
    world = pinned_world()
    if world is not None:
        return _frozen_date_mismatch(world, snapshot_date) or _frozen_get_adp(world, player_id, history_days)

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

def _frozen_get_team_context(world: str, team: str, season: int) -> dict:
    """Same real team_stats aggregation as live mode, but only for seasons
    completed BEFORE the world date -- asking about the world's own (not yet
    played) season is refused as a leakage attempt, not served from the
    future."""
    season_cap = world_season(world)
    if season >= season_cap:
        return _unavailable(
            "nflverse team_stats (frozen-world filtered)",
            f"season {season} has not been played as of world {world} -- only seasons < {season_cap} exist here",
        )
    physical = physical_gold_snapshot_for_world()
    try:
        df = load_raw_table(physical, "nflverse", "team_stats")
    except FileNotFoundError:
        return _unavailable(
            "nflverse team_stats",
            f"no team_stats table in {physical}'s snapshot -- run `python -m capture.pull_stats` (.venv311)",
        )
    df = _reg_only(df)
    team_rows = df[(df["team"] == team) & (df["season"] == season)]
    if len(team_rows) == 0:
        return _unavailable(
            "nflverse team_stats (frozen-world filtered)",
            f"no {season} rows for team={team!r} (season data not pulled -- pre-2024 seasons deferred per D-014)",
        )
    total_plays = team_rows["attempts"] + team_rows["carries"]
    values = {
        "games": len(team_rows),
        "plays_per_game": round(float(total_plays.mean()), 2),
        "pass_rate": round(float(team_rows["attempts"].sum() / total_plays.sum()), 4),
        "passing_epa_per_game": round(float(team_rows["passing_epa"].mean()), 4),
        "rushing_epa_per_game": round(float(team_rows["rushing_epa"].mean()), 4),
        "proe": None,       # unavailable -- same reason as live mode (see get_team_context docstring)
        "win_total": None,  # unavailable -- and doubly so historically: no archived free source either
        "ol_rank": None,    # unavailable -- same reason as live mode
    }
    return _response(
        values,
        f"nflverse team_stats, season {season} (frozen world {world}; physical table from GOLD snapshot "
        f"{physical} -- completed-season facts, identical whenever captured)",
        world,
        schema_version("nflverse_team_stats"),
        note="proe/win_total/ol_rank are genuinely unavailable (see get_team_context docstring), not zero or estimated",
    )


def get_team_context(team: str, season: int, snapshot_date: str | None = None) -> dict:
    """Plays/game and pass rate come from real nflverse team_stats. PROE
    (pass rate OVER EXPECTED -- needs a play-calling model conditioned on
    score/time/down-distance, not just raw pass rate) and Vegas win total
    (a betting-market product nflverse doesn't carry, and D-006 rules out
    a paid odds API) and OL rank (not a raw stat -- that's a paid analyst
    ranking like PFF's) are reported as genuinely unavailable per field,
    not guessed or approximated with something that looks similar but isn't.
    """
    world = pinned_world()
    if world is not None:
        return _frozen_date_mismatch(world, snapshot_date) or _frozen_get_team_context(world, team, season)

    pinned_date = resolve_snapshot_date(snapshot_date)
    try:
        df = load_raw_table(pinned_date, "nflverse", "team_stats")
    except FileNotFoundError:
        return _unavailable(
            "nflverse team_stats",
            f"no team_stats table in {pinned_date}'s snapshot -- run `python -m capture.pull_stats` (.venv311)",
        )

    df = _reg_only(df)
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

def _world_depth_chart_as_of(world: str, team: str) -> str | None:
    """Per-team Wayback capture date for a world's depth chart. Coverage is
    UNEVEN (e.g. the 2025-06-18 world spans 2025-02-20 to 2025-06-17 across
    teams -- a February chart predates that year's free agency), so the
    per-team as-of date is part of the citation, not buried in a manifest."""
    manifest_path = (
        Path("backtest/frozen_worlds") / world / "raw" / "ourlads_depth_chart" / "manifest.json"
    )
    if not manifest_path.exists():
        return None
    ts = json.loads(manifest_path.read_text()).get("source", {}).get("per_team_source_timestamp", {}).get(team)
    if not ts or len(ts) < 8:
        return None
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"


def _frozen_get_depth_chart(world: str, team: str) -> dict:
    """Serves the world's archived Ourlads depth chart (Wayback, 32/32 teams,
    skill positions only -- QB/RB/WR/TE). Same entry shape as live mode;
    injury_status is None because no as-of-world-date injury source exists
    (serving TODAY'S injury status inside a backtest would be leakage)."""
    dc = load_world_table(world, "ourlads_depth_chart", "depth_chart_proposed")
    team_rows = dc[dc["team"] == team].copy()
    if len(team_rows) == 0:
        return _unavailable(
            "Ourlads depth charts via Wayback Machine (frozen world)",
            f"no depth chart rows for team={team!r} in world {world} (32 NFL team codes, LAR not LA)",
        )
    team_rows["depth_order"] = pd.to_numeric(team_rows["depth_order"], errors="coerce")
    team_rows = team_rows.sort_values(["position", "depth_chart_position_raw", "depth_order"])
    entries = [
        {
            "gsis_id": r["gsis_id"],
            "full_name": r["player_name"],
            "position": r["position"],
            "depth_chart_position": r["depth_chart_position_raw"],
            "depth_chart_order": None if pd.isna(r["depth_order"]) else int(r["depth_order"]),
            "injury_status": None,
        }
        for _, r in team_rows.iterrows()
    ]
    as_of = _world_depth_chart_as_of(world, team)
    return _response(
        entries,
        f"Ourlads depth charts via Wayback Machine (frozen world; {team} chart captured {as_of or 'unknown date'})",
        world,
        None,
        note=f"this team's chart is as-of {as_of or 'an unrecorded date'}, not necessarily the world date -- "
        "per-team Wayback coverage is uneven and a pre-March chart predates that year's free agency; "
        "skill positions only (QB/RB/WR/TE); injury_status is None (no as-of-world-date injury source exists)",
    )


def get_depth_chart(team: str, snapshot_date: str | None = None) -> dict:
    """Sleeper's raw player table carries depth_chart_position/_order per
    player -- usable directly, no separate nflverse depth-chart pull needed."""
    world = pinned_world()
    if world is not None:
        return _frozen_date_mismatch(world, snapshot_date) or _frozen_get_depth_chart(world, team)

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

def _frozen_get_vacated_opportunity(world: str, team: str, season: int | None) -> dict:
    """Live mode detects departures via Sleeper's CURRENT team field -- which
    is future information relative to a frozen world and would be leakage
    here. Frozen mode instead cross-checks prior-season volume against the
    world's own archived depth chart: a player with {season} volume on `team`
    who is absent from that team's as-of-world-date depth chart counts as
    departed. Caveat (stated in the note, not hidden): Ourlads lists ~4-6
    players per skill position, so a deep-bench player with minor volume can
    be counted departed when he's merely unlisted -- an as-of-date roster
    limitation, acceptable because vacated-opportunity analysis cares about
    meaningful volume, which listed players carry.
    """
    season_cap = world_season(world)
    if season is None:
        season = season_cap - 1
    if season >= season_cap:
        return _unavailable(
            "frozen world",
            f"season {season} has not been played as of world {world} -- only seasons < {season_cap} exist here",
        )
    physical = physical_gold_snapshot_for_world()
    try:
        stats_df = load_raw_table(physical, "nflverse", "player_stats")
    except FileNotFoundError:
        return _unavailable(
            "nflverse player_stats",
            f"no player_stats table in {physical}'s snapshot -- run `python -m capture.pull_stats` (.venv311)",
        )
    stats_df = _reg_only(stats_df)
    stats_df = stats_df[stats_df["season"] < season_cap]
    team_season_rows = stats_df[
        (stats_df["season"] == season) & (stats_df["team"] == team)
        & stats_df["position"].isin(["QB", "RB", "WR", "TE"])
    ]
    if len(team_season_rows) == 0:
        return _unavailable(
            "nflverse player_stats (frozen-world filtered)",
            f"no {season} rows for team={team!r} (season data not pulled -- pre-2024 seasons deferred per D-014)",
        )

    agg = (
        team_season_rows.groupby("player_id")
        .agg(targets=("targets", "sum"), carries=("carries", "sum"), name=("player_display_name", "first"))
        .reset_index()
    )
    agg = agg[(agg["targets"] > 0) | (agg["carries"] > 0)]

    dc = load_world_table(world, "ourlads_depth_chart", "depth_chart_proposed")
    on_chart = set(dc[dc["team"] == team]["gsis_id"].dropna())
    if not on_chart:
        return _unavailable(
            "Ourlads depth charts via Wayback Machine (frozen world)",
            f"no depth chart for team={team!r} in world {world} -- cannot determine departures",
        )
    departed = agg[~agg["player_id"].isin(on_chart)].sort_values("targets", ascending=False)

    values = {
        "vacated_targets": int(departed["targets"].sum()),
        "vacated_carries": int(departed["carries"].sum()),
        "departed_players": [
            {
                "gsis_id": r["player_id"],
                "name": r["name"],
                f"{season}_targets": int(r["targets"]),
                f"{season}_carries": int(r["carries"]),
                "on_world_depth_chart": False,
            }
            for _, r in departed.iterrows()
        ],
    }
    as_of = _world_depth_chart_as_of(world, team)
    return _response(
        values,
        f"nflverse player_stats ({season}) vs. world {world}'s archived Ourlads depth chart "
        f"({team} chart captured {as_of or 'unknown date'})",
        world,
        schema_version("nflverse_player_stats"),
        note=f"'departed' means absent from {team!r}'s depth chart as captured {as_of or 'on an unrecorded date'} "
        "(skill positions only, ~4-6 listed per position) -- NOT Sleeper's live team field, which would be future "
        "information here. A pre-March chart predates that year's free agency and will MISS offseason departures; "
        "deep-bench players with minor volume may be counted departed when merely unlisted",
    )


def get_vacated_opportunity(team: str, season: int | None = None, snapshot_date: str | None = None) -> dict:
    """Targets/carries vacated by players who had volume on `team` in
    `season` (default: most recent season pulled) but aren't on `team` per
    Sleeper's CURRENT team field -- reuses sleeper_resolved (already
    live-updated day to day) rather than a separate nflverse rosters pull,
    since "who's on the team right now" is exactly what Sleeper's own team
    field already tracks. A player showing no current team (retired, or a
    free agent Sleeper hasn't attached to a roster) counts as departed too.
    """
    world = pinned_world()
    if world is not None:
        return _frozen_date_mismatch(world, snapshot_date) or _frozen_get_vacated_opportunity(world, team, season)

    pinned_date = resolve_snapshot_date(snapshot_date)
    try:
        stats_df = load_raw_table(pinned_date, "nflverse", "player_stats")
    except FileNotFoundError:
        return _unavailable(
            "nflverse player_stats",
            f"no player_stats table in {pinned_date}'s snapshot -- run `python -m capture.pull_stats` (.venv311)",
        )

    stats_df = _reg_only(stats_df)
    if season is None:
        season = int(stats_df["season"].max())

    team_season_rows = stats_df[
        (stats_df["season"] == season) & (stats_df["team"] == team) & stats_df["position"].notna()
    ]
    if len(team_season_rows) == 0:
        return _unavailable("nflverse player_stats", f"no {season} rows for team={team!r}")

    agg = (
        team_season_rows.groupby("player_id")
        .agg(targets=("targets", "sum"), carries=("carries", "sum"), name=("player_display_name", "first"))
        .reset_index()
    )
    agg = agg[(agg["targets"] > 0) | (agg["carries"] > 0)]

    try:
        sleeper_df = load_curated_table(pinned_date, "sleeper_resolved")
    except FileNotFoundError:
        return _unavailable("sleeper_resolved", f"no sleeper_resolved table in {pinned_date}'s snapshot")

    current_team_by_gsis = sleeper_df.set_index("gsis_id")["team"].to_dict()
    agg["current_team"] = agg["player_id"].map(current_team_by_gsis)
    departed = agg[agg["current_team"] != team].sort_values("targets", ascending=False)

    values = {
        "vacated_targets": int(departed["targets"].sum()),
        "vacated_carries": int(departed["carries"].sum()),
        "departed_players": [
            {
                "gsis_id": r["player_id"],
                "name": r["name"],
                f"{season}_targets": int(r["targets"]),
                f"{season}_carries": int(r["carries"]),
                "current_team": None if pd.isna(r["current_team"]) else r["current_team"],
            }
            for _, r in departed.iterrows()
        ],
    }
    return _response(
        values,
        f"nflverse player_stats ({season}) vs. Sleeper's current team field",
        pinned_date,
        schema_version("nflverse_player_stats"),
        note=f"'departed' means not currently on {team!r} per Sleeper's live team field as of {pinned_date} "
        "-- includes trades, free-agent signings elsewhere, retirements, and unsigned free agents alike",
    )


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
    world = pinned_world()
    if world is not None:
        return _unavailable(
            "nflverse crosswalk bio fields",
            f"get_comps is unavailable in frozen world {world}: the crosswalk's bio fields are present-day "
            "(age not as-of the world date; the comp pool includes draft classes that postdate the world). "
            "An as-of-date comps source is deferred until Agent 3 (profile_analyst) is built -- D-014",
        )

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
    world = pinned_world()
    return _response(
        {
            "team_count": latest_check["summary"]["team_count"],
            "roster_slots": latest_check["summary"]["roster_slots"],
            "scoring_settings": latest_check["raw_settings"]["settings"]["scoringSettings"],
        },
        f"charter.md §5, cross-verified against ESPN league {ESPN_LEAGUE_ID} settings ({checks[-1].stem})",
        None,
        None,
        note=None if world is None else (
            f"served identically in frozen world {world}: charter scoring is the fixed lens for the whole "
            "project -- backtest ground truth is scored under this exact function's settings, so this is "
            "not a leak, it's the shared measuring stick"
        ),
    )


# --- list_data_gaps ----------------------------------------------------

_FROZEN_STRUCTURAL_GAPS = [
    "injury_status: no as-of-world-date injury source exists (frozen depth chart entries carry None)",
    "adp_history: frozen worlds hold a single archived ADP snapshot -- no movement/trend data",
    "trending_adds_drops: Sleeper trending is live-only, structurally absent from frozen worlds",
    "comps: get_comps unavailable in frozen mode (present-day bio fields would leak -- see D-014)",
    "current_season_data: the world's own season is unplayed as of the world date, by definition",
]


def _frozen_list_data_gaps(world: str, player_id: str) -> dict:
    gaps = []
    present = []
    for source, table, label in [
        ("fantasypros_adp", "adp", "frozen_adp"),
        ("fantasypros_ecr", "ecr", "frozen_ecr"),
        ("ourlads_depth_chart", "depth_chart_proposed", "frozen_depth_chart"),
    ]:
        try:
            df = load_world_table(world, source, table)
            if player_id in set(df["gsis_id"].dropna()):
                present.append(label)
            else:
                gaps.append(f"{label}: gsis_id not found in world {world}")
        except FileNotFoundError:
            gaps.append(f"{label}: table does not exist in world {world}")

    season_cap = world_season(world)
    physical = physical_gold_snapshot_for_world()
    try:
        ws = _reg_only(load_curated_table(physical, "weekly_stats"))
        ws = ws[ws["season"] < season_cap]
        if len(ws) == 0:
            gaps.append(f"weekly_stats: no pre-{season_cap} seasons pulled (2023 deferred per D-014)")
        elif player_id in set(ws["player_id"].dropna()):
            present.append(f"weekly_stats (seasons < {season_cap})")
        else:
            gaps.append(f"weekly_stats: no pre-{season_cap} rows (rookie as of {world}, or didn't play)")
    except FileNotFoundError:
        gaps.append(f"weekly_stats: table does not exist in {physical}'s snapshot")

    gaps.extend(_FROZEN_STRUCTURAL_GAPS)
    return _response(
        {"present_in": present, "gaps": gaps},
        f"cross-table presence check (frozen world {world})",
        world,
        None,
    )


def list_data_gaps(player_id: str, snapshot_date: str | None = None) -> dict:
    """What's missing for this player, so agents can report honestly (§7) --
    checked against every table this project actually produces, not a
    hardcoded list that goes stale."""
    world = pinned_world()
    if world is not None:
        return _frozen_date_mismatch(world, snapshot_date) or _frozen_list_data_gaps(world, player_id)

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

    gaps.append(STILL_UNPULLED_SOURCES_NOTE)

    return _response(
        {"present_in": present, "gaps": gaps},
        "cross-table presence check",
        pinned_date,
        None,
    )
