# PROJECT BRIEF — Multi-Agent Fantasy Football Research System
**Owner:** Nick | **Brief version:** 1.2 (2026-07-18) | **Update this header whenever status changes**

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

**Phase: 0 COMPLETE (charter approved 2026-07-18). Phase 1 build IN PROGRESS.**
**Current step: Tier 1 daily capture job (Sleeper player meta/injury/trending + ADP) is built and locally verified for 2026-07-18. Repo is `git init`'d locally only — NOT yet pushed to a GitHub remote, so the Actions cron in `.github/workflows/daily-capture.yml` is not yet actually running. NEXT: (a) push to a GitHub remote so the cron goes live — every day without it running is un-backfillable ADP history, (b) the ESPN reader (settings diff + primary ADP, D-005), (c) the canonical-ID crosswalk and curated-layer/validation pipeline (§3, §6) that the raw capture job intentionally does not yet build.**

| Phase | Design doc | Build status |
|---|---|---|
| WBS (all phases) | fantasy-ai-project-wbs.md | n/a |
| 0 Charter | phase0-charter-design.md | ✅ COMPLETE — charter.md approved; decisions.md live (D-001–D-007) |
| 1 Data platform | phase1-data-platform-design.md | ☐ In progress — Tier 1 raw capture (Sleeper + FFC ADP) built (`capture/`), verified locally 2026-07-18. NOT yet: pushed to GitHub (cron inert until then), ESPN reader, crosswalk, curated layer, full validation pipeline (Stage 2/3), GOLD marking. |
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

## 4. Success Criteria (summary — binding formulas live in phase0 doc, Appendix M)
- SM1: My Guys hit rate ≥ 60% (dual condition: beat ADP-implied finish AND startable value)
- SM2: board's rank correlation vs. actuals beats BOTH ADP and ECR, overall + ≥3 of 4 positions
- SM3: tier accuracy (±1 tier) ≥ 70%
- SM4: process — backtest gate passed pre-draft, capture ≥90% uptime Jul 24→draft, zero uncited stats in deliverables

## 5. Artifact Inventory
- Nine design docs (§2 table) — binding; deviations recorded in decisions.md, designs never edited retroactively
- **charter.md** — APPROVED 2026-07-18; §3/§4/§5 frozen (12-team ESPN, 1.0 PPR, 4pt pass TD, 7 bench, redraft snake, draft Aug 29/30)
- **decisions.md** — append-only log, D-001 through D-006 live
- Repo location: ______ (fill in once created in Claude Code)
- Data snapshot location: /data/snapshots/ in repo per phase1 design §4
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
2. ~~**Build Tier 1 daily capture — Sleeper first** (ADP, injury, trending → dated immutable snapshots per phase1 §4) + GitHub Actions cron at 06:00 ET~~ Code done and locally verified 2026-07-18 (`capture/`, ADP via FantasyFootballCalculator per D-007, not Sleeper — see decision). **STILL OPEN: push this repo to a GitHub remote and confirm the first green Actions run** — the cron does nothing until the repo exists on GitHub with Actions enabled. Target: live well before Jul 24 (M1).
3. **ESPN reader:** pull league 94172663 settings with owner cookies, diff against charter §5; ESPN becomes the PRIMARY ADP source once built (D-005), demoting today's FFC pull to secondary
4. Continue Phase 1 per design doc: canonical-ID crosswalk (§3), data dictionary (§5), full validation pipeline Stage 2/3 + GOLD marking (§6), curated layer, agent access layer (§7) — none of these exist yet, only raw capture does

## 8. Project History (one paragraph)
Conceived July 2026 from Nick's idea of month-long multi-agent research with debate. Research reshaped it: static-data re-analysis adds nothing (daily → trigger-based monitoring), persona debate adds nothing (→ methodology-differentiated agents + adjudication with bias controls), and unmeasured systems can't be trusted (→ eval-first: charter metrics, frozen-world backtests, and baseline bars before any agent runs). All eight phases were designed with per-phase research during July 2026. Phase 0 executed 2026-07-18: charter approved (12-team ESPN PPR league, draft Aug 29/30), decision log seeded D-001–D-006 including two deviations (ESPN as platform/primary market; zero-API-spend execution under the Claude plan). Build begins at Phase 1 with the daily capture job.
