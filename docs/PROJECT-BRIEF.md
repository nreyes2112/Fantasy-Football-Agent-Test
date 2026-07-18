# PROJECT BRIEF — Multi-Agent Fantasy Football Research System
**Owner:** Nick | **Brief version:** 1.11 (2026-07-18) | **Update this header whenever status changes**

> **Purpose of this document:** Load this at the start of any new Claude conversation about this project. It carries the project's history, decisions, current status, and working rules so any new chat can continue without re-explaining. It is a living document — the STATUS and NEXT ACTIONS sections should be updated as work progresses; everything else changes rarely.

---

## 1. What This Project Is

A data-grounded, multi-agent AI system that produces fantasy football draft rankings (position ranks, tiers, overall value board) and a falsifiable "My Guys" list that measurably beats market consensus (ADP/ECR) for Nick's league. Success is scored in January against pre-registered criteria. The system compounds season over season: each season is archived as a new backtest world.

**Design philosophy (settled — do not relitigate in new chats):**
- Evals before agents: the backtest harness and baselines exist before anything smart is built; every design decision resolves against a scored gate, not opinion
- Tool-grounded data only: no LLM-recalled statistics anywhere; uncited claims are invalid
- Agents differ by methodology, not persona; debate exists only if it beats simple averaging on frozen worlds
- Pre-registration everywhere: theses, falsifiers, metric formulas, and price caps are written before outcomes are known
- Beat the market or ship the market: if the system can't clear ADP/ECR retroactively, draft off ADP

## 2. Current Status (UPDATE THIS SECTION)

**Phase: 0 COMPLETE (charter approved 2026-07-18). Phase 1 build essentially complete -- 6 of 8 exit-checklist items done.**
**Current step: The actual curated layer is built — `capture/build_curated_stats.py` produces `curated/weekly_stats.parquet` (player_stats joined with snap_counts_resolved's snap_share), the table phase1 §5's data dictionary literally names as several metrics' source. `get_player_stats()` now reads from this curated table instead of raw player_stats directly, matching the design's architecture (raw -> curated -> serve) for real, not just in the identity-resolution sense. Verified: row counts match exactly (no join fan-out), and both hand-checked PPG values (Josh Allen 38.76, Jahmyr Gibbs 15.0) and snap_share (Gibbs 0.67) reproduce identically through the new path.**
**Checking Phase 1's exit checklist (phase1-data-platform-design.md §9) against everything built: 6 of 8 items are DONE (GOLD snapshot exists; league scoring implemented + hand-verified; data dictionary v1 committed; agent tool layer answers with citations; backup ADP source named+tested; decision log updated). The remaining 2 are NOT code gaps: (1) "daily ADP snapshot job verified for 3+ consecutive days" needs real calendar time to pass (the cron has only been live since earlier today) -- nothing to build, just time; (2) "crosswalk covers 100% of the Charter universe, unmatched queue empty" needs Nick's human review of ~6 ambiguous FFC name-match proposals (`data/snapshots/<date>/curated/ffc_unmatched_queue.parquet`) -- by design, this project never auto-confirms a name match, so this is a decision only the owner can make, not something to build around.**
**NEXT: given the above, there is no more Phase 1 *code* to write for the checklist itself. Options: (a) start Phase 2 (backtest harness) design/build, which doesn't depend on either remaining item; (b) close the data dictionary's last 5 metrics (route_participation/YPRR need `load_participation`; success_rate/PROE need `load_pbp`) if that's a priority before Phase 2; (c) wait for the cron + get Nick's review of the unmatched queue to formally close out Phase 1's checklist.**

| Phase | Design doc | Build status |
|---|---|---|
| WBS (all phases) | fantasy-ai-project-wbs.md | n/a |
| 0 Charter | phase0-charter-design.md | ✅ COMPLETE — charter.md approved; decisions.md live (D-001–D-008) |
| 1 Data platform | phase1-data-platform-design.md | ☐ 6/8 exit-checklist items DONE — raw capture, canonical-ID crosswalk (§3) incl. snap_counts, full validation + GOLD marking (§6), the curated layer (`curated/weekly_stats`), ALL 8 agent access-layer functions (§7), and the data dictionary (§5, 11/18 metrics computable) all built and (capture side) LIVE on GitHub Actions cron. Remaining 2 checklist items are time (3-day capture streak) and a human decision (FFC unmatched-queue review), not code. NOT built: nflverse pbp/participation/full-rosters/draft-combine (not currently blocking anything). |
| 2 Backtest harness | phase2-backtest-harness-design.md | ☐ Not started |
| 3 Agents (5 prompts) | phase3-agent-prompts.md | ☐ Not started |
| 4 Debate + judge | phase4-debate-protocol.md | ☐ Not started |
| 5 Weekly ops | phase5-ops-weekly-design.md | ☐ Not started |
| 6 Deliverables | phase6-deliverables-design.md | ☐ Not started |
| 7 Postmortem | phase7-postmortem-design.md | ☐ Runs January |

**Key dates (from charter §6):** M1 capture live Jul 24 · M2 baselines Jul 31 · M5 debate gate Aug 19 · M7 board freeze Aug 27 · Draft Aug 29/30.

## 3. Key Decisions Already Made (with rationale — do not reopen without cause)

1. **nflreadpy, not nfl_data_py** — nfl_data_py is deprecated; nflreadpy is the maintained nflverse Python library. Any doc referencing nfl_data_py is superseded on this point. (D-002)
2. **Weekly Thursday-morning analysis brief** (not daily) — matches decision tempo and prevents alert fatigue. BUT data capture (ADP/injury/depth chart snapshots) stays DAILY and silent — it is un-backfillable. Draft-week ramp: daily analysis from Aug 19.
3. **Five agents split by methodology:** opportunity/volume, efficiency (regression-mandatory), profile/comps, market/ADP, team-context (top-down). Subject to kill/merge if one adds no unique signal. (D-004)
4. **Debate: 2-round cap, agenda = top disagreements only, adjudicated by a judge on a different model/config**, with anti-bias controls (randomized order, length caps, uncited claims struck). Decision gate: debate must beat accuracy-weighted averaging on frozen worlds or it gets cut.
5. **Frozen worlds 2024-07-15 and 2025-07-15** for backtesting; each completed season becomes a new world. (D-003)
6. **Tiers via Gaussian mixture (BIC-selected count) on projection + uncertainty**, min-gap post-processing; overall board via value-over-replacement with a VOLS-leaning, league-calibrated baseline.
7. **My Guys = mechanical selection** (board-vs-ADP delta + multi-agent agreement + surviving thesis), 5-10 players, each with pre-registered thesis, max-reach price cap, and invalidation trigger.
8. **Board freezes at draft − 2 days** (= Aug 27); post-freeze events are annotations, never regenerations.
9. **Postmortem in January** scores everything against the charter; separates decision quality from outcome quality (no "resulting"); every finding becomes a decision-log action or explicit no-action.
10. **ESPN is the league platform and PRIMARY market signal** (league 94172663, private). ESPN reader is the league-settings source of truth (diffs against charter §5) and primary ADP for DELTA/My Guys pricing; Sleeper demoted to secondary/backup ADP (satisfies risk R2); FantasyPros ECR unchanged. Auth via owner's SWID/espn_s2 cookies, local-only, never committed. Supersedes the Phase 1 design's Sleeper-as-platform assumption. (D-005)
11. **Zero API spend.** Agent/debate/backtest runs execute inside Claude Code sessions under Nick's Claude plan; the daily capture job runs on GitHub Actions free-tier cron (independent of any personal machine). Budget controls = agenda/round/word caps; capture takes priority over analysis if plan limits are hit. Supersedes the Phase 0 design's API-budget line. (D-006)
12. **Sleeper has no ADP endpoint.** Verified against Sleeper's actual API (docs.sleeper.com): it exposes player meta/injury status and trending adds/drops only. FantasyFootballCalculator's free keyless API is the ADP source instead, scoped to this league's format. Supersedes phase1-data-platform-design.md §2's "Sleeper API → ADP" line and charter §10 R2's backup-source name. (D-007)
13. **ESPN's real API host is `lm-api-reads.fantasy.espn.com`, not `fantasy.espn.com`.** The commonly-documented host now sits behind an AWS WAF JS challenge a plain HTTP client can't pass. Verified 2026-07-18 against league 94172663: `lm-api-reads` returns real data on the same `view` params, and every charter §5 field matched on the first settings-diff run. (D-008)

## 4. Success Criteria (summary — binding formulas live in phase0 doc, Appendix M)
- SM1: My Guys hit rate ≥ 60% (dual condition: beat ADP-implied finish AND startable value)
- SM2: board's rank correlation vs. actuals beats BOTH ADP and ECR, overall + ≥3 of 4 positions
- SM3: tier accuracy (±1 tier) ≥ 70%
- SM4: process — backtest gate passed pre-draft, capture ≥90% uptime Jul 24→draft, zero uncited stats in deliverables

## 5. Artifact Inventory
- Nine design docs (§2 table) — binding; deviations recorded in decisions.md, designs never edited retroactively
- **charter.md** — APPROVED 2026-07-18; §3/§4/§5 frozen (12-team ESPN, 1.0 PPR, 4pt pass TD, 7 bench, redraft snake, draft Aug 29/30)
- **decisions.md** — append-only log, D-001 through D-008 live
- Repo location: https://github.com/nreyes2112/Fantasy-Football-Agent-Test
- Data snapshot location: /data/snapshots/ in repo per phase1 design §4
- **docs/data-dictionary.md** — phase1 §5's required metric definitions; every derived metric an agent or the backtest harness will ever need is defined ONCE here, not in a prompt
**New chats: read this brief, then the charter, then the design doc for the phase being worked — the designs are binding.**

## 6. Working Rules for Any Claude Session on This Project
1. Read this brief first; then read the design doc for the phase being worked.
2. Follow the designs. Deviations are allowed but must be flagged as deviations, justified, and recorded as a decision-log entry (D-###) citing what changed.
3. Never fabricate stats, ADP values, or player data — retrieve or mark unavailable.
4. Verify anything time-sensitive (library status, API endpoints, player news) with current sources; this project has already caught one deprecated-library issue.
5. When producing build artifacts (code, configs), state which design-doc section they implement.
6. Scope guard: v1 is pre-draft season-long rankings for Nick's league only. In-season lineup tools, trade calculators, DFS, other leagues = out of scope without a charter change.
7. Keep this brief current: when a phase's status changes or a decision is made, update §2/§3 and bump the version header.

## 7. Next Actions (UPDATE THIS SECTION)
1. ~~**Claude Code:** create project folder (charter.md, decisions.md, docs/), `/init` the repo, verify/install Python~~ DONE 2026-07-18
2. ~~**Build Tier 1 daily capture — Sleeper first** (ADP, injury, trending → dated immutable snapshots per phase1 §4) + GitHub Actions cron at 06:00 ET~~ DONE 2026-07-18 — pushed to GitHub (https://github.com/nreyes2112/Fantasy-Football-Agent-Test), first end-to-end Actions run (pull → commit → push) confirmed green. ADP via FantasyFootballCalculator per D-007, not Sleeper. Well ahead of Jul 24 (M1).
3. ~~**ESPN reader:** pull league 94172663 settings with owner cookies, diff against charter §5; ESPN becomes the PRIMARY ADP source once built (D-005)~~ DONE 2026-07-18 — settings match, ESPN ADP live on the daily cron with `ESPN_SWID`/`ESPN_S2` as GitHub repo secrets.
4. ~~**Canonical-ID crosswalk (§3):** resolve Sleeper/ESPN/FFC to nflverse `gsis_id`~~ DONE 2026-07-18 — `capture/crosswalk.py` + `capture/pull_crosswalk.py`, weekly cron (`.github/workflows/weekly-crosswalk.yml`, needs a separate Python ≥3.10 venv for `nflreadpy` — see CLAUDE.md Commands). Sleeper/ESPN resolve deterministically; FFC is proposal-only per design. **STILL OPEN:** human review of the ~6 skill-position players in the unmatched queue (`data/snapshots/<date>/curated/ffc_unmatched_queue.parquet`) — 2 are genuine cross-player name collisions (Lamar Jackson QB/CB, DJ Moore WR/CB), 1 is a nickname mismatch (Kenny/Kenneth Gainwell), 3 are just outside FFC's captured ADP depth.
5. ~~**Full validation pipeline Stage 2/3 + GOLD marking (§6)**~~ DONE 2026-07-18 — `capture/validation.py`, wired into `pull_crosswalk.py` (runs post-curation, since several checks need identity resolution first). First GOLD snapshot 2026-07-18 (22/22 checks), verified locally and on GitHub Actions.
6. ~~**Read-only agent access layer (§7)**~~ DONE 2026-07-18 — `access/layer.py` + `access/snapshot_resolver.py`. All 8 functions from the design exist and are pinned to the latest GOLD snapshot (refuses non-GOLD dates). Tested against real data (Jahmyr Gibbs' ADP history, Detroit's RB depth chart, bio-similarity comps, the full ESPN scoring table). 3 of 8 (`get_player_stats`, `get_team_context`, `get_vacated_opportunity`) correctly report `available: False` pending nflverse stat pulls below — by design, not a bug.
7. ~~**nflverse player_stats pull (§2)**~~ DONE 2026-07-18 — `capture/pull_stats.py`, weekly (same workflow as the crosswalk). 2024-2025 weekly stats (145 columns incl. target_share, air_yards_share, wopr, EPA), `player_id` already the canonical gsis_id (no crosswalk join needed). `get_player_stats()` now reads this for real (season/last4/last8 windows) instead of reporting unavailable. Verified against Jahmyr Gibbs' real last-8-games stats, locally and on GitHub Actions.
8. ~~**nflverse team_stats pull (§2)**~~ DONE 2026-07-18 — `capture/pull_stats.py` extended (same script, refactored into a shared `_pull_and_validate()` helper). 2024-2025 weekly team stats (133 columns). Caught a real team-code mismatch: nflverse's `load_team_stats` uses "LA" for the Rams vs. this project's "LAR" convention everywhere else — normalized at the source. `get_team_context()` now returns real plays/game, pass rate, EPA/game — verified against Detroit and Baltimore's real 2025 profiles. PROE, Vegas win total, and OL rank remain genuinely unavailable per field (no free source for any of the three — not a pull gap, a real data-availability ceiling).
9. ~~**get_vacated_opportunity (§7, last access-layer stub)**~~ DONE 2026-07-18 — reuses player_stats + sleeper_resolved's live team field (no separate nflverse rosters pull needed): sums a departed player's prior-season target/carry volume by checking whether they're still on the team per Sleeper. Verified against real 2025→2026 departures (Denver, SF, MIN, NYJ, ATL all checked). Caught and fixed a real bug while building this: the LA/LAR (Rams) team-code fix from item 8 above had only been applied to team_stats, not player_stats — same quirk, same fix now applied to both, re-verified. **All 8 access-layer functions now return real data.**
10. ~~**Data dictionary (§5)**~~ DONE 2026-07-18 — [docs/data-dictionary.md](data-dictionary.md), all 18 required metrics defined, 10 implemented in `access/metrics.py` (aDOT, EPA_per_target, TD_rate, carry_share, draft_capital_tier, plus the flagship league-accurate PPG). **PPG hand-verified against 2 independent real stat lines exactly as the design demands** (Josh Allen 38.76/38.76, Jahmyr Gibbs 15.0/15.0). 6 metrics honestly marked not-yet-computable (5 need one more nflverse pull; `age_curve_position` is a deferred methodology decision, not a data gap).
11. ~~**nflverse snap_counts pull (§2) + snap_share (§5)**~~ DONE 2026-07-18 — `capture/sources/nflverse.py` + `capture/pull_crosswalk.py` (resolved here, not `pull_stats.py`, since it needs the crosswalk table already loaded there: this source is `pfr_player_id`-keyed, resolved via `nflverse_crosswalk.pfr_id` reusing the existing `resolve_source()` machinery). `offense_pct` IS `snap_share` directly, no derivation needed. Verified: Josh Allen 98.11% (full-time starter), Jahmyr Gibbs 67% (sensibly above his 54.98% carry_share). Data dictionary now 11/18 computable.
12. ~~**Curated layer beyond the crosswalk (§1)**~~ DONE 2026-07-18 — `capture/build_curated_stats.py` produces `curated/weekly_stats.parquet` (player_stats + snap_counts_resolved's snap_share, joined on gsis_id/season/week, zero fan-out verified). `get_player_stats()` now reads from it. **Phase 1 exit checklist (§9): 6 of 8 items done** — the remaining 2 (3-day capture streak; FFC unmatched-queue human review) are time/human-decision items, not code. See §2 above for options on what's next.
13. **Decision point, not yet started:** start Phase 2 (backtest harness) design/build — doesn't depend on either remaining Phase 1 checklist item — OR close the data dictionary's last 5 metrics first (`load_participation`/`load_nextgen_stats` for route_participation/YPRR; `load_pbp` for success_rate/PROE) OR full rosters/draft-combine. None of these block each other; this is a sequencing choice for Nick, not a technical dependency.

## 8. Project History (one paragraph)
Conceived July 2026 from Nick's idea of month-long multi-agent research with debate. Research reshaped it: static-data re-analysis adds nothing (daily → trigger-based monitoring), persona debate adds nothing (→ methodology-differentiated agents + adjudication with bias controls), and unmeasured systems can't be trusted (→ eval-first: charter metrics, frozen-world backtests, and baseline bars before any agent runs). All eight phases were designed with per-phase research during July 2026. Phase 0 executed 2026-07-18: charter approved (12-team ESPN PPR league, draft Aug 29/30), decision log seeded D-001–D-006 including two deviations (ESPN as platform/primary market; zero-API-spend execution under the Claude plan). Build begins at Phase 1 with the daily capture job.
