# Phase 1 — Data Platform Design
## Sources, Canonical IDs, Snapshots, Validation, and the Agent Access Layer

---

## Design Notes (how the research shaped this)

- **Immutable raw snapshots are the foundation.** APIs and SaaS sources can change data retroactively, so best practice is dated snapshots of pulled data (daily/weekly) to preserve history, plus content hashing to detect silent changes. This is doubly critical here: ADP is the one dataset that *cannot* be reconstructed later, and point-in-time correctness is what keeps Phase 2's frozen worlds leakage-free.
- **Validate early, in stages.** Guidance: catch errors as close to the source as possible; start with schema and null checks at ingestion, then semantic checks, then statistical checks in later stages — and treat validation rules as versioned, tested code, not ad hoc scripts.
- **Version the whole pipeline state, not just data.** Reproducibility requires versioning the transformation code (Git), the schemas (stored alongside the data), and the data itself; hardcoded date-range queries are the error-prone anti-pattern that snapshots exist to replace.
- **⚠️ Library correction (supersedes WBS/Phase 3 references to `nfl_data_py`):** `nfl_data_py` has been deprecated in favor of `nflreadpy`, with no further maintenance planned. `nflreadpy` is the nflverse team's Python port of nflreadr — same data, `load_` function naming, Polars dataframes (convertible to pandas), built-in caching. **This project standardizes on `nflreadpy`**; keep `nfl_data_py` only as an emergency fallback for its `import_ids()` crosswalk if needed. Most nflverse data is CC-BY 4.0 licensed — fine for personal use with attribution.

---

## 1. Architecture Overview

```
 SOURCES                INGEST              STORE                     SERVE
┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ nflverse      │   │ pull_*.py    │   │ /data/snapshots/  │   │ Agent tool layer │
│ (nflreadpy)   │──▶│ per source   │──▶│   YYYY-MM-DD/     │──▶│ get_player_stats │
│ Sleeper API   │   │              │   │     raw/          │   │ get_adp          │
│ FantasyPros   │   │ staged       │   │     curated/      │   │ get_team_context │
│ Vegas totals  │   │ validation   │   │     manifest.json │   │ get_comps        │
│ Injury/news   │   │ (as code)    │   │   GOLD marker     │   │ (read-only,      │
└──────────────┘   └──────────────┘   └──────────────────┘   │  cite-by-default)│
                                                              └──────────────────┘
```
Principles: raw is immutable and append-only; curated is rebuilt from raw by versioned code; agents read ONLY through the serve layer, pinned to one gold snapshot.

## 2. Source Specifications

| Source | What | Cadence | Notes |
|---|---|---|---|
| nflreadpy `load_pbp` | Play-by-play (EPA, air yards, etc.) | Weekly in-season; once for historical | Large; cache locally |
| nflreadpy `load_player_stats` | Weekly/seasonal player stats | Weekly | Primary stat source |
| nflreadpy rosters/depth charts | Roster, depth chart, snap counts | Weekly; daily in camp | Depth chart churn is a Phase 5 signal |
| nflreadpy schedules/win totals | Schedule, Vegas win totals | Weekly | context_analyst input |
| nflreadpy draft/combine/contracts | Draft capital, measurables, contracts | Once per offseason | profile_analyst input |
| Sleeper API | ADP, trending adds/drops, player meta, injury status | **Daily (non-negotiable)** | Free, no key; the un-backfillable dataset |
| FantasyPros | ECR, ADP consensus | Daily | Respect ToS; scrape gently or manual CSV export if needed |
| Injury/news | Official designations + aggregated news | Daily (Phase 5 consumes) | Store as text with source + timestamp |

Backup ADP source (Charter risk R2): name it here: ______ (e.g., a second platform's ADP endpoint).

## 3. Canonical Player ID Specification

The unglamorous task that breaks everything if skipped — every source uses different IDs (gsis, Sleeper, FantasyPros, PFR).

- **Canonical key:** nflverse `gsis_id` (stable, appears throughout nflverse data).
- **Crosswalk table:** built from nflverse ID mappings (nflreadpy player datasets; fallback: `nfl_data_py.import_ids()`, which maps players across most major NFL and fantasy platforms). Rebuilt weekly; stored in every snapshot.
- **Name matching is a last resort**, used only to *propose* crosswalk entries; proposals land in an unmatched-queue for human confirmation, never silently auto-joined (name collisions and Jr./III suffixes are classic corruption vectors).
- **Rookies:** enter the crosswalk at draft ingest with draft capital attached; Sleeper IDs joined as they appear.
- **Acceptance metric:** 100% of the Charter's fixed player universe (QB24/RB48/WR60/TE24 by consensus) resolved to canonical IDs across all active sources; unmatched-queue empty before any agent run.

## 4. Snapshot & Versioning Specification

```
/data
  /snapshots
    /2026-07-17
      /raw/{source}/{table}.parquet      # exactly as pulled, immutable
      /curated/{table}.parquet           # rebuilt by versioned code
      manifest.json                      # see below
      GOLD                               # empty marker file, written only
                                         # after validation passes
  /schemas/{table}/v{n}.json             # schema versions stored alongside data
  /frozen_worlds
    /2024-07-15 -> (assembled per Phase 2 spec from raw archives)
    /2025-07-15
```

- **manifest.json** per snapshot: pull timestamps, source versions, row counts, SHA-256 content hashes per file, code git commit, schema versions used, validation report reference. Hashes make silent upstream changes detectable.
- **Immutability rules:** nothing under a dated snapshot is ever edited. Corrections happen by cutting a new snapshot. The GOLD marker is written once and never moved.
- **Retention:** raw ADP/injury/depth-chart snapshots are kept forever (small, irreplaceable). Bulky pbp raw can be kept once per week + manifest hashes.
- **Schema changes:** new schema version file + decision-log entry; curated rebuild code must state which schema versions it accepts.

## 5. Data Dictionary & Derived Metrics

One YAML/markdown file, versioned in Git, defining every derived metric ONCE. Agents and harness both read definitions from here — no metric is ever defined inside a prompt or notebook.

Required entries (initial set, one block each):

```
metric: target_share
definition: player targets / team pass attempts, per game window
formula: sum(targets) / sum(team_pass_attempts)
windows: [season, last8, last4, post_event]   # post_event = role-change window
source_tables: [curated/weekly_stats]
stability: HIGH        # stable/predictive classification, used by agents
notes: exclude games with <25% snap share from windows
```

Initial dictionary must cover: target_share, carry_share, snap_share, route_participation, weighted_opportunity (state the red-zone/end-zone weights), YPRR, aDOT, air_yards_share, EPA_per_target, success_rate, TD_rate (stability: LOW — regression-mandatory flag), pass_rate_over_expectation, team_plays_per_game, vacated_targets/carries, age_curve_position, draft_capital_tier, ADP fields (raw, 7/14/30-day deltas), PPG under the league's exact scoring (§5 of Charter — implement the league's scoring function here, test it against a hand-computed example).

**Stability tags matter:** the efficiency agent's regression-to-mean mandate and the judge's "stable metrics outweigh unstable ones" rubric line both key off this field — so the classification lives in data, not vibes.

## 6. Validation Pipeline (validation-as-code, staged)

Stage gates — a snapshot only gets its GOLD marker when all stages pass; failures quarantine the snapshot and alert.

**Stage 1 — Schema & completeness (at ingest):**
- Schema conformance vs. current schema version; required columns non-null
- Row-count sanity vs. trailing snapshots (e.g., weekly stats within ±15% of expected)
- Player universe coverage: every Charter-universe player present in stats, ADP, and crosswalk

**Stage 2 — Semantic (post-curation):**
- Range checks: shares in [0,1]; no negative counting stats; ages plausible
- Referential integrity: every curated row's player_id resolves in the crosswalk; team codes valid for the season (relocations handled)
- Cross-source agreement: season totals from weekly rollup vs. seasonal table within tolerance; ADP from two sources within a sanity band

**Stage 3 — Statistical (drift/anomaly):**
- Distribution drift vs. prior snapshot (flag, don't block, unless extreme)
- ADP day-over-day movement outliers → routed to Phase 5 as signal, and checked here as possible data error (the same spike is one or the other)
- Hash comparison: unchanged-source files whose hashes changed anyway → investigate silent restatement

Validation rules live in the repo, have unit tests, and are version-tagged; a rule change is a decision-log entry (a rule tweak that changes what passes is a regression you must be able to bisect).

## 7. Agent Access Layer (the ONLY path to numbers)

Read-only tool functions, all pinned to one gold snapshot per run, all returning citation payloads:

```
get_player_stats(player_id, metrics[], window) ->
  {values: {...}, source, snapshot_date, schema_version}
get_adp(player_id, history_days) -> {...}
get_team_context(team, season) -> {...}          # plays, PROE, win total, OL rank
get_depth_chart(team) -> {...}
get_vacated_opportunity(team) -> {...}
get_comps(player_id, features[], k) -> {...}     # profile_analyst similarity tool
get_league_scoring() -> the Charter scoring function
list_data_gaps(player_id) -> what's missing, so agents can report honestly
```

Rules:
- Tool responses embed `{source, snapshot_date}` so agent citations are automatic (Phase 3's citation rule becomes copy-through, not memory).
- No free-form SQL/dataframe access for agents in v1 — a fixed tool surface keeps outputs comparable across agents and runs.
- The harness (Phase 2) calls the same layer with the snapshot pinned to a frozen world — identical code path for backtest and production is what makes backtests honest.

## 8. Jobs & Schedule

| Job | Cadence | Output |
|---|---|---|
| ADP + injury + trending pull | Daily (starts at Charter signing) | raw snapshot rows |
| Depth chart + news pull | Daily from camp open | raw snapshot rows |
| Full nflverse refresh | Weekly (daily in-season if extended later) | raw + curated |
| Crosswalk rebuild + unmatched queue | Weekly | curated + review list |
| Validation + GOLD marking | On every snapshot | manifest + report |
| Frozen-world assembly | Once (Phase 2 kickoff) | /frozen_worlds |

## 9. Phase 1 Exit Checklist
- [ ] Daily ADP snapshot job running and verified for 3+ consecutive days
- [ ] One full GOLD snapshot exists (all stages passed)
- [ ] Crosswalk covers 100% of the Charter player universe; unmatched queue empty
- [ ] League scoring function implemented and verified against one hand-scored player-week
- [ ] Data dictionary v1 committed with stability tags
- [ ] Agent tool layer answers: "Player X's target share, last 8 games?" with citation payload
- [ ] Backup ADP source named and test-pulled once (Charter R2)
- [ ] Decision log updated: D-002 amended to nflreadpy (supersedes nfl_data_py)
