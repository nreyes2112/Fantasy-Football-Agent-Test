"""Canonical player-ID crosswalk build (phase1-data-platform-design.md §3).

Resolves each source's own player id to nflverse gsis_id (the canonical
key):
  - Sleeper and ESPN resolve deterministically via the nflverse/DynastyProcess
    crosswalk's own sleeper_id/espn_id columns -- no name matching involved.
  - FantasyFootballCalculator has no shared ID with anything else, so it can
    only be *proposed* a match by normalized-name equality. Per §3, name
    matches are proposals only -- they land in the unmatched/needs-review
    queue and are never written into the crosswalk as confirmed rows.

Output of build_crosswalk(): a resolved table (one row per gsis_id with
whatever source ids matched) and a queue of rows that didn't resolve
deterministically, for human confirmation.
"""

from __future__ import annotations

import re

import pandas as pd

_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?$", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^a-z0-9 ]")


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    n = name.lower().strip()
    n = _PUNCT_RE.sub("", n)
    n = _SUFFIX_RE.sub("", n).strip()
    n = re.sub(r"\s+", " ", n)
    return n


def _clean_id(value) -> str | None:
    """nflreadpy's crosswalk stores platform ids as float64 (e.g. 13269.0),
    while Sleeper/ESPN's own tables use clean integer-like ids ("13269",
    4429795). A plain .astype(str) on the float column would produce
    "13269.0" and silently fail every join -- strip the trailing .0 first.
    """
    if pd.isna(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _resolve_by_id(source_df: pd.DataFrame, source_id_col: str, crosswalk: pd.DataFrame, crosswalk_id_col: str) -> pd.DataFrame:
    """Left-join source rows onto the crosswalk via a shared platform id.
    Both id columns are normalized to clean integer-like strings first --
    see _clean_id."""
    left = source_df.copy()
    if "gsis_id" in left.columns:
        # e.g. Sleeper's own raw table already carries a (sometimes null,
        # self-reported) gsis_id -- keep it for comparison but don't let it
        # collide with the crosswalk's resolved gsis_id.
        left = left.rename(columns={"gsis_id": "gsis_id_source_reported"})
    left["_join_id"] = left[source_id_col].map(_clean_id)
    right = crosswalk[[crosswalk_id_col, "gsis_id"]].dropna(subset=[crosswalk_id_col]).copy()
    right["_join_id"] = right[crosswalk_id_col].map(_clean_id)
    merged = left.merge(right[["_join_id", "gsis_id"]], on="_join_id", how="left")
    return merged.drop(columns=["_join_id"])


def resolve_source(
    source_df: pd.DataFrame, source_id_col: str, crosswalk: pd.DataFrame, crosswalk_id_col: str
) -> tuple[pd.DataFrame, dict]:
    """Returns (resolved_df, coverage_stats). resolved_df has a gsis_id
    column, null where the platform id wasn't found in the crosswalk."""
    resolved = _resolve_by_id(source_df, source_id_col, crosswalk, crosswalk_id_col)
    total = len(resolved)
    matched = resolved["gsis_id"].notna().sum()
    stats = {
        "method": f"deterministic id join ({crosswalk_id_col})",
        "total_rows": int(total),
        "resolved_rows": int(matched),
        "coverage_pct": round(100.0 * matched / total, 2) if total else 0.0,
    }
    return resolved, stats


def propose_by_name(
    source_df: pd.DataFrame, name_col: str, crosswalk: pd.DataFrame, crosswalk_name_col: str = "merge_name"
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Name-based matching is a PROPOSAL, never an auto-confirmed join (§3).

    Returns (proposed_matches, unmatched_queue, coverage_stats). Callers must
    NOT treat proposed_matches as resolved -- they need human confirmation
    before being merged into the crosswalk.
    """
    left = source_df.copy()
    left["_norm_name"] = left[name_col].map(normalize_name)

    right = crosswalk[[crosswalk_name_col, "gsis_id"]].dropna(subset=["gsis_id"]).copy()
    right["_norm_name"] = right[crosswalk_name_col].map(normalize_name)
    # A normalized name matching more than one gsis_id is ambiguous -- drop
    # it from consideration rather than guess.
    dupe_names = right["_norm_name"].value_counts()
    ambiguous_names = set(dupe_names[dupe_names > 1].index)
    right = right[~right["_norm_name"].isin(ambiguous_names)]

    merged = left.merge(
        right[["_norm_name", "gsis_id"]], on="_norm_name", how="left", suffixes=("", "_proposed")
    )
    proposed = merged[merged["gsis_id"].notna()].drop(columns=["_norm_name"])
    unmatched = merged[merged["gsis_id"].isna()].drop(columns=["gsis_id", "_norm_name"])

    total = len(merged)
    stats = {
        "method": f"proposed name match ({name_col} vs {crosswalk_name_col}, ambiguous names excluded)",
        "total_rows": int(total),
        "proposed_rows": int(len(proposed)),
        "unmatched_rows": int(len(unmatched)),
        "proposed_pct": round(100.0 * len(proposed) / total, 2) if total else 0.0,
    }
    return proposed, unmatched, stats


def charter_universe_coverage(
    espn_resolved: pd.DataFrame,
    universe_sizes: dict[str, int],
    other_sources: dict[str, pd.DataFrame],
) -> list[dict]:
    """Acceptance metric per charter.md §5 / phase1 §3: 100% of the fixed
    player universe (QB24/RB48/WR60/TE24 "by consensus") must resolve to a
    canonical gsis_id ACROSS ALL ACTIVE SOURCES -- not just independently in
    each one. Pre-freeze, "by consensus" is approximated using ESPN's own
    ADP-sorted rank per position (kona_player_info is returned pre-sorted by
    draft rank) -- a real consensus board doesn't exist until later phases,
    so this is a build-time proxy, not the final freeze-time check.

    `other_sources`: {source_name: resolved_or_proposed_df}, each of which
    must have a `gsis_id` column (resolve_source's output, or
    propose_by_name's `proposed` output -- the latter is a proposal, so a
    "match" here means "a human-confirmable candidate exists", not "confirmed").
    """
    report = []
    for position, size in universe_sizes.items():
        pos_rows = espn_resolved[espn_resolved["position"] == position].head(size)
        universe_gsis = set(pos_rows["gsis_id"].dropna())

        row = {
            "position": position,
            "universe_size": size,
            "rows_available": len(pos_rows),
            "espn_resolved": len(universe_gsis),
        }
        intersection = set(universe_gsis)
        for source_name, source_df in other_sources.items():
            source_gsis = set(source_df["gsis_id"].dropna())
            row[f"{source_name}_found"] = len(universe_gsis & source_gsis)
            intersection &= source_gsis

        row["fully_resolved_across_all_sources"] = len(intersection)
        row["coverage_pct"] = round(100.0 * len(intersection) / len(pos_rows), 2) if len(pos_rows) else 0.0
        report.append(row)
    return report
