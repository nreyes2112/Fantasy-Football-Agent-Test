# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A design-and-build repo for a multi-agent AI system that produces fantasy football draft rankings and a "My Guys" list for one specific ESPN league (12-team, 1.0 PPR, redraft, draft ~Aug 29/30 2026), scored against ADP/ECR in a January postmortem. **As of this writing there is no code yet** — the repo contains an approved charter, a decision log, and nine binding design documents. Phase 1 (data platform / daily capture job) is the next thing to be built. Do not assume a build system, test runner, or source tree exists — check before referencing one.

## Reading order (do this before making changes)

1. [docs/PROJECT-BRIEF.md](docs/PROJECT-BRIEF.md) — living status doc: current phase, next actions, settled design philosophy. Read this first every session.
2. [charter.md](charter.md) — the frozen objectives, success metrics, scope, and league settings (§3/§4/§5 are frozen; changes require a decision-log entry).
3. [decisions.md](decisions.md) — append-only decision log (D-001…D-006). Entries are never edited; superseded decisions get a new entry linking back.
4. The design doc for whichever phase is being worked (`docs/phaseN-*.md`).

The design docs are **binding**, not suggestions. Deviating from one is allowed but must be flagged as a deviation, justified, and recorded as a new decision-log entry citing which doc/section it supersedes.

## Working rules (from PROJECT-BRIEF.md §6 — apply every session)

- Never fabricate stats, ADP values, or player data. Retrieve from a tool/source or mark it explicitly unavailable — this applies to Claude's own output, not just agent code being built.
- Verify anything time-sensitive (library status, API endpoints, player news) against current sources rather than assumed knowledge — this project has already caught one deprecated-library issue (`nfl_data_py` → `nflreadpy`, D-002).
- When producing build artifacts (code, configs), state which design-doc section they implement.
- Scope guard: v1 is pre-draft season-long rankings for this one league only. Weekly lineup/start-sit advice, trade valuation, DFS/betting, other leagues, and K/DST/IDP rankings are out of scope without a charter change.
- Zero API spend is a hard constraint (D-006): all data sources must be free (nflverse CC-BY, Sleeper keyless, ESPN unofficial API); agent/debate/backtest runs execute inside Claude Code sessions, not a metered API; the only unattended component is the daily capture job, intended for GitHub Actions free-tier cron.
- ESPN cookies (SWID/espn_s2) for private-league access are local-only secrets — never commit them.
- Update [docs/PROJECT-BRIEF.md](docs/PROJECT-BRIEF.md) §2/§3 (status, decisions) and bump its version header when a phase's status changes.

## Design philosophy (settled — do not relitigate)

- **Evals before agents.** The backtest harness and naive baselines (ADP/ECR) exist before anything "smart" is built; every architectural choice must clear a scored gate, not opinion. "Beat the market or ship the market" — if the system can't beat ADP/ECR retroactively on frozen worlds, the deliverable is ADP with annotations.
- **Tool-grounded data only.** No LLM-recalled statistics anywhere. Every quantitative claim carries a citation `{metric, value, source, snapshot_date}`; uncited claims are struck in debate and invalid in deliverables.
- **Agents differ by methodology, not persona.** Five analysts split by methodology/data emphasis (opportunity/volume, efficiency, profile/comps, market/ADP, team-context) — subject to kill/merge if one adds no unique signal.
- **Pre-registration everywhere.** Theses, falsifiers (`what_would_change_my_mind`), metric formulas, and price caps are written down before outcomes are known — the anti-hindsight-bias and anti-groupthink mechanism.
- **Debate must earn its cost.** It's kept only if it beats simple accuracy-weighted averaging on frozen-world backtests; 2-round hard cap; judge runs on a different model/config than the debaters to reduce self-preference bias.
- **Cadence matches decision tempo, not data volume.** Data *capture* is daily and silent (un-backfillable — the one thing that must never lapse). Analysis/briefing is weekly (Thursday mornings), except a daily ramp during draft week (Aug 19+).
- **Blameless, systems-shaped postmortem.** Findings are framed as "what was missing in the system," not "which agent was bad." Every finding terminates in a decision-log entry, a config change, or an explicit no-action — and outcome is scored separately from decision quality (anti-"resulting").

## Architecture (as designed — build order is Phase 1 → 7)

**Phase 1 — Data platform** ([docs/phase1-data-platform-design.md](docs/phase1-data-platform-design.md)): sources → ingest → immutable dated snapshots → a read-only agent access layer.
- Sources: `nflreadpy` (nflverse: pbp, player stats, rosters/depth charts, schedules, draft/combine — **not** `nfl_data_py`, which is deprecated and kept only as an emergency fallback for its `import_ids()` crosswalk), Sleeper API (ADP/trending/injury, daily, non-negotiable), FantasyPros (ECR), ESPN unofficial API (league settings source of truth + primary ADP per D-005).
- Canonical player ID = nflverse `gsis_id`, with a weekly-rebuilt crosswalk. Name matching is proposal-only, human-confirmed — never silently auto-joined.
- Storage: `/data/snapshots/YYYY-MM-DD/{raw,curated}/` + `manifest.json` (hashes, row counts, code commit, schema versions) + a `GOLD` marker written only after validation passes. Raw is immutable and append-only; curated is rebuilt from raw by versioned code.
- Validation is staged and gated: schema/completeness → semantic (range/referential/cross-source) → statistical (drift/anomaly). A rule change is itself a decision-log entry.
- Agents reach data **only** through a fixed read-only tool surface (`get_player_stats`, `get_adp`, `get_team_context`, `get_depth_chart`, `get_comps`, `get_league_scoring`, `list_data_gaps`, etc.), pinned to one gold snapshot per run, every response carrying `{source, snapshot_date}`. No free-form SQL/dataframe access for agents.

**Phase 2 — Backtest harness** ([docs/phase2-backtest-harness-design.md](docs/phase2-backtest-harness-design.md)): frozen worlds (2024-07-15, 2025-07-15, growing walk-forward each season) reconstructed with strict as-of-date data and a leakage audit checklist; candidates (agents/ensemble/baseline) call the **same** Phase 1 access layer, just pinned to a frozen snapshot — identical code path for backtest and production. Every config run 3+ times (LLM output variance); results tied to a config-hash fingerprint for bisectability. Must beat naive baselines or the system ships the baseline instead.

**Phase 3 — Agent prompts** ([docs/phase3-agent-prompts.md](docs/phase3-agent-prompts.md)): shared CORE block (data discipline, citation rules, output schema, one worked example) prepended to five methodology-specific prompts. Each agent runs as a chain — pull data → compute/rank → justify — and must state pre-registered falsifiers per contested player before seeing other agents' work.

**Phase 4 — Debate protocol** ([docs/phase4-debate-protocol.md](docs/phase4-debate-protocol.md)): agenda selection (which players get debated) and turn order are code, not agent choices. 2-round cap: Round 0 = pre-registered positions (Phase 3), Round 1 = challenge & defend with full reasoning chains, Round 2 = final revision (any change must cite the specific evidence that triggered it). Judge adjudicates on a different model/config, with randomized presentation order and length caps to counter position/verbosity bias.

**Phase 5 — Weekly ops** ([docs/phase5-ops-weekly-design.md](docs/phase5-ops-weekly-design.md)): Tier 1 (daily, silent capture) vs. Tier 2 (Thursday-morning diff + triggered single-player re-evaluations, never a full-board rebuild, + changelog + brief) vs. a narrow interrupt channel for the small set of act-before-Thursday events.

**Phase 6 — Deliverables** ([docs/phase6-deliverables-design.md](docs/phase6-deliverables-design.md)): position rankings → Gaussian-mixture tiers (BIC-selected count, min-gap post-processing, `BOUNDARY` flags) → cross-position overall board via value-over-replacement (`VALUE = projected points − positional replacement baseline`, DELTA = ADP − board rank is the action column) → mechanically-selected My Guys (board-vs-ADP delta + agreement + surviving thesis). Board freezes at draft − 2 days; anything after is an annotation, never a regeneration.

**Phase 7 — Postmortem** ([docs/phase7-postmortem-design.md](docs/phase7-postmortem-design.md), runs January): score the charter's SM1-SM4 exactly as pre-registered → decision-quality-vs-outcome 2×2 (anti-resulting) → per-agent/judge/debate attribution feeding next season's weights → RCA of worst misses. Every input is a frozen artifact; no memory allowed.

## Key binding decisions (see decisions.md for full rationale)

- **D-002:** `nflreadpy`, not `nfl_data_py`; canonical ID = `gsis_id`.
- **D-003:** Frozen backtest worlds at 2024-07-15 and 2025-07-15.
- **D-004:** Five agents split by methodology (not persona); build Agent 1 (opportunity) end-to-end first, template the rest from it.
- **D-005 (deviation from Phase 1 design):** ESPN, not Sleeper, is the league platform and PRIMARY ADP source. Sleeper is secondary/backup. FantasyPros ECR unchanged.
- **D-006 (deviation from Phase 0 design):** Zero API spend — everything runs inside Claude Code sessions under the existing plan; only the daily capture job is unattended cron.
