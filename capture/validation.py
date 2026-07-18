"""Validation pipeline (phase1-data-platform-design.md §6), staged.

Stage 1 (schema & completeness) is re-affirmed here using the schema
dictionaries pull_daily.py already wrote, plus the raw stage1-lite result
pull_daily.py recorded in manifest.json. Stage 2 (semantic) and Stage 3
(statistical) run against the crosswalk-resolved tables, since several of
their checks (referential integrity to gsis_id, cross-source ADP agreement)
only make sense once identity resolution has happened -- which is why this
runs from pull_crosswalk.py, not pull_daily.py.

A snapshot's GOLD marker (§4) is written only when every stage's checks
pass. The crosswalk's own completeness (100% of the charter universe
resolved across sources) is a SEPARATE metric per the Phase 1 exit
checklist -- GOLD is about data quality, not identity-resolution coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _check(stage: int, name: str, passed: bool, detail: str) -> dict:
    return {"stage": stage, "check": name, "passed": bool(passed), "detail": detail}


# Sleeper/ESPN's team-abbreviation convention. NOT the same as nflverse's
# `team` column (verified 2026-07-18: nflverse uses GBP/JAC/KCC/NEP/NOS/SFO/
# TBB plus historical codes like OAK/SDC/STL for relocated teams) -- so this
# is hardcoded from the modern 32-team vocabulary rather than derived from
# the crosswalk, which would otherwise flag every legitimate Sleeper team
# code as "unknown".
CURRENT_NFL_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
    "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS", "FA",
}


def stage1_schema_completeness(tables: dict[str, pd.DataFrame], schema_root: Path) -> list[dict]:
    """Re-affirms Stage 1 (already lightly checked at ingest by pull_daily.py):
    every documented column is actually present in what was written."""
    checks = []
    for table_name, df in tables.items():
        schema_path = schema_root / table_name / "v1.json"
        if not schema_path.exists():
            checks.append(_check(1, f"{table_name}_schema_documented", False, f"no schema file at {schema_path}"))
            continue
        documented_cols = set(json.loads(schema_path.read_text())["columns"].keys())
        documented_cols.discard("...")  # nflverse_crosswalk's schema note uses this as a placeholder, not a real column
        missing = documented_cols - set(df.columns)
        checks.append(
            _check(
                1,
                f"{table_name}_columns_match_schema",
                len(missing) == 0,
                f"missing columns: {sorted(missing)}" if missing else "all documented columns present",
            )
        )
    return checks


def _range_check(name: str, series: pd.Series, low, high, max_violation_pct: float = 0.0) -> dict:
    """max_violation_pct allows a small tolerance for sources that mix in
    known-irrelevant junk -- e.g. Sleeper's full player dump includes
    decades of retired/historical entries with garbage ages, which this
    project doesn't care about (out of the charter-universe scope) and
    shouldn't block GOLD on its own."""
    values = series.dropna()
    if len(values) == 0:
        return _check(2, name, True, "no non-null values to check")
    out_of_range = values[(values < low) | (values > (high if high is not None else values.max() + 1))]
    violation_pct = 100.0 * len(out_of_range) / len(values)
    return _check(
        2, name, violation_pct <= max_violation_pct,
        f"{len(out_of_range)}/{len(values)} values ({violation_pct:.2f}%) outside [{low}, {high}] (tolerance {max_violation_pct}%)"
        if len(out_of_range) else f"all {len(values)} values within [{low}, {high}]",
    )


def stage2_semantic(
    sleeper_resolved: pd.DataFrame,
    espn_resolved: pd.DataFrame,
    ffc_df: pd.DataFrame,
    ffc_proposed: pd.DataFrame,
    nflverse_df: pd.DataFrame,
) -> list[dict]:
    checks = []

    # Range checks
    checks.append(_range_check("espn_percent_owned_in_0_100", espn_resolved["percent_owned"], 0, 100))
    checks.append(_range_check("espn_percent_started_in_0_100", espn_resolved["percent_started"], 0, 100))
    checks.append(_range_check("espn_adp_positive", espn_resolved["average_draft_position"], 0, None))
    checks.append(_range_check("ffc_adp_positive", ffc_df["adp"], 0, None))
    # Sleeper's full player dump spans decades of historical/retired entries
    # this project doesn't care about (verified 2026-07-18: 5/10960, e.g.
    # players from the 1980s-90s with stale "Active" status) -- a small
    # tolerance keeps this check meaningful without demanding the entire
    # historical Sleeper ID space be clean.
    checks.append(_range_check("sleeper_age_plausible", sleeper_resolved["age"], 18, 50, max_violation_pct=0.2))

    # Referential integrity: team codes actually seen must be valid current
    # NFL abbreviations. NOT checked against nflverse's own `team` column --
    # verified 2026-07-18 that nflverse uses a different convention entirely
    # (GBP/JAC/KCC/NEP/NOS/SFO/TBB, plus historical codes for relocated
    # teams), which would falsely flag every legitimate Sleeper team code.
    # A tiny row-count tolerance (not just a set-membership check) absorbs
    # the same kind of stale historical entries as the age check above --
    # verified 2026-07-18: exactly 1 row with team="OAK" (a since-renamed
    # Las Vegas), a single inactive legacy record, not a broken pull.
    team_col = sleeper_resolved["team"].dropna()
    unknown_teams = set(team_col.unique()) - CURRENT_NFL_TEAMS
    unknown_row_count = team_col.isin(unknown_teams).sum()
    checks.append(
        _check(
            2, "sleeper_team_codes_known", unknown_row_count <= 5,
            f"{unknown_row_count} rows with unrecognized team codes: {sorted(unknown_teams)}"
            if unknown_teams else "all team codes recognized",
        )
    )

    # Cross-source ADP agreement: for players resolved in BOTH ESPN and FFC,
    # ESPN's ADP and FFC's ADP shouldn't wildly diverge. Some divergence is
    # expected (different drafter populations, D-005). Measured 2026-07-18
    # by round bucket before picking these thresholds (not guessed):
    # ESPN ADP 0-50 -> 0% diverge >24 picks; 50-100 -> 27%; 100-150 -> 39%;
    # 150-220 -> 23%. The clean boundary is specifically the top ~50 picks
    # (roughly the first 4 rounds in a 12-team league) -- that's where board
    # integrity for My Guys/DELTA pricing matters most, so it gets a tight
    # bound; everything deeper gets a much looser one reflecting real,
    # observed cross-market noise rather than an arbitrary flat cutoff.
    espn_adp = espn_resolved[["gsis_id", "average_draft_position"]].dropna(subset=["gsis_id"])
    ffc_adp = ffc_proposed[["gsis_id", "adp"]].dropna(subset=["gsis_id"])
    merged = espn_adp.merge(ffc_adp, on="gsis_id", how="inner")
    if len(merged) == 0:
        checks.append(_check(2, "espn_ffc_adp_agreement_premium", True, "no players resolved in both sources yet"))
        checks.append(_check(2, "espn_ffc_adp_agreement_overall", True, "no players resolved in both sources yet"))
    else:
        merged["divergence"] = (merged["average_draft_position"] - merged["adp"]).abs()
        threshold = 24.0  # ~2 rounds in a 12-team league

        premium = merged[merged["average_draft_position"] < 50]
        if len(premium):
            divergent_premium = premium[premium["divergence"] > threshold]
            pct_premium = 100.0 * len(divergent_premium) / len(premium)
            checks.append(
                _check(
                    2, "espn_ffc_adp_agreement_premium", pct_premium <= 10.0,
                    f"{len(divergent_premium)}/{len(premium)} players ({pct_premium:.1f}%) with ESPN ADP<50 diverge >{threshold:.0f} picks from FFC",
                )
            )
        else:
            checks.append(_check(2, "espn_ffc_adp_agreement_premium", True, "no matched players with ESPN ADP<50 yet"))

        divergent_all = merged[merged["divergence"] > threshold]
        pct_all = 100.0 * len(divergent_all) / len(merged)
        checks.append(
            _check(
                2, "espn_ffc_adp_agreement_overall", pct_all <= 45.0,
                f"{len(divergent_all)}/{len(merged)} matched players ({pct_all:.1f}%) diverge >{threshold:.0f} picks overall "
                "(higher tolerance than the premium bucket -- late-round cross-market noise is expected, not an error)",
            )
        )

    return checks


def stage3_statistical(snapshot_root: Path, today: str, espn_df: pd.DataFrame) -> list[dict]:
    """Drift/anomaly checks. Advisory per the design ("flag, don't block,
    unless extreme") -- these are informational and don't gate GOLD, except
    where noted."""
    checks = []
    prior_dates = sorted(
        d.name for d in snapshot_root.iterdir() if d.is_dir() and d.name < today
    )
    if not prior_dates:
        checks.append(_check(3, "adp_drift_vs_prior_day", True, "first snapshot -- no prior day to compare yet"))
        return checks

    prior_date = prior_dates[-1]
    prior_path = snapshot_root / prior_date / "raw" / "espn" / "player_pool.parquet"
    if not prior_path.exists():
        checks.append(_check(3, "adp_drift_vs_prior_day", True, f"no ESPN raw table in {prior_date} to compare against"))
        return checks

    prior_espn = pd.read_parquet(prior_path)
    merged = espn_df[["player_id", "average_draft_position"]].merge(
        prior_espn[["player_id", "average_draft_position"]], on="player_id", suffixes=("_today", "_prior")
    )
    merged["delta"] = (merged["average_draft_position_today"] - merged["average_draft_position_prior"]).abs()
    outlier_threshold = 20.0
    outliers = merged[merged["delta"] > outlier_threshold]
    # Informational (per design) unless the outlier rate itself looks like a
    # data error rather than real market movement.
    passed = len(outliers) < max(10, 0.10 * len(merged))
    checks.append(
        _check(
            3, "adp_drift_vs_prior_day", passed,
            f"{len(outliers)}/{len(merged)} players moved >{outlier_threshold:.0f} ADP picks vs {prior_date} "
            "-- informational (Phase 5 signal), not necessarily a data error unless the rate itself is extreme",
        )
    )
    return checks
