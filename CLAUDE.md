# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A design-and-build repo for a multi-agent AI system that produces fantasy football draft rankings and a "My Guys" list for one specific ESPN league (12-team, 1.0 PPR, redraft, draft ~Aug 29/30 2026), scored against ADP/ECR in a January postmortem. The repo contains an approved charter, a decision log, nine binding design documents, and (as of Phase 1 build-out) a working `capture/` package: daily raw-data capture (Sleeper, FantasyPros-alternative ADP, ESPN) running live on GitHub Actions, plus a weekly canonical player-ID crosswalk. Phases 2–7 (backtest harness, agents, debate, ops, deliverables, postmortem) are still just design docs — check `docs/PROJECT-BRIEF.md` §2 for the current build status before assuming something exists.

## Commands

Two separate virtualenvs, because `nflreadpy` requires Python ≥3.10 while the rest of the capture code was built against the system's Python 3.9:

- **`.venv`** (Python 3.9) — Sleeper/FFC/ESPN capture, and the agent access layer:
  ```
  source .venv/bin/activate
  pip install -r requirements.txt
  python -m capture.pull_daily            # Tier 1 daily capture (idempotent per ET date)
  python -m capture.espn_settings_check   # one-off: diff live ESPN league settings vs charter.md §5
  python -c "from access import layer; print(layer.get_adp('<gsis_id>'))"  # agent access layer (§7); see access/layer.py
  ```
- **`.venv311`** (Python ≥3.10, e.g. via `brew install python@3.11`) — nflverse crosswalk + player stats:
  ```
  source .venv311/bin/activate
  pip install -r requirements.txt -r requirements-crosswalk.txt
  python -m capture.pull_crosswalk        # requires today's raw snapshot to already exist (run pull_daily first)
  python -m capture.pull_stats            # weekly nflverse player_stats pull (2024-2025); also requires today's raw snapshot
  ```
  `pull_crosswalk`'s exit code is non-zero when charter-universe coverage isn't 100% across all sources — that's an expected, informative signal (e.g. unconfirmed name matches), not necessarily a bug; check `data/snapshots/<date>/curated_manifest.json` for the breakdown. `pull_stats` writes its own `nflverse_stats_manifest.json` (not yet folded into the GOLD marker — a known scope gap).

Secrets (`ESPN_SWID`, `ESPN_S2`) live in a local, gitignored `.env` and as encrypted GitHub Actions repository secrets — never hardcode or commit them. `.github/workflows/daily-capture.yml` and `.github/workflows/weekly-crosswalk.yml` run these jobs unattended.

## Reading order (do this before making changes)

1. [docs/PROJECT-BRIEF.md](docs/PROJECT-BRIEF.md) — living status doc: current phase, next actions, settled design philosophy. Read this first every session.
2. [charter.md](charter.md) — the frozen objectives, success metrics, scope, and league settings (§3/§4/§5 are frozen; changes require a decision-log entry).
3. [decisions.md](decisions.md) — append-only decision log (D-001…D-008). Entries are never edited; superseded decisions get a new entry linking back.
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
- Sources actually pulled today (`capture/sources/`): Sleeper (player meta/injury/trending — it has **no ADP endpoint** despite the design doc, D-007), FantasyFootballCalculator (free ADP, secondary market source), ESPN (this league's own ADP — **primary** market source per D-005, plus settings verification against charter §5). ESPN's real API host is `lm-api-reads.fantasy.espn.com`, not the commonly-documented `fantasy.espn.com`, which now sits behind an AWS WAF challenge (D-008). `nflreadpy` (**not** `nfl_data_py`, deprecated) is used for `load_ff_playerids()` (the crosswalk source), `load_player_stats()`, and `load_team_stats()` (2024-2025 weekly, `capture/pull_stats.py`) — `player_id` in player_stats is already the canonical `gsis_id`, verified 2026-07-18, no crosswalk join needed; `load_team_stats`'s own "LA" team code is normalized to this project's "LAR" convention at the source. Pbp, rosters/depth-charts, and draft/combine aren't pulled yet.
- Canonical player ID = nflverse `gsis_id`. `capture/crosswalk.py` + `capture/pull_crosswalk.py` resolve Sleeper and ESPN deterministically via the DynastyProcess/nflreadpy crosswalk's own `sleeper_id`/`espn_id` columns; FFC has no shared ID, so it only gets a *proposed* name match (never auto-confirmed) that lands in an unmatched-review queue if ambiguous or absent. Acceptance metric (100% of charter's QB24/RB48/WR60/TE24 resolved across all sources) is checked but pre-freeze uses ESPN's ADP order as a consensus-board stand-in.
- Storage: `/data/snapshots/YYYY-MM-DD/{raw,curated}/` + `manifest.json` (raw pulls) / `curated_manifest.json` (crosswalk + validation output) with hashes, row counts, code commit. Raw manifests are never rewritten once written — the crosswalk gets its own separate manifest file specifically so it can run after the fact without touching raw's immutability.
- Validation (`capture/validation.py`, run from `pull_crosswalk.py` since several checks need identity resolution first) is staged per §6: Stage 1 re-affirms schema completeness against `data/schemas/{table}/v1.json`; Stage 2 does range/referential-integrity/cross-source-ADP-agreement checks; Stage 3 does day-over-day drift (informational, degrades gracefully with <2 days of history). A `GOLD` marker is written only when every stage passes — first achieved 2026-07-18. GOLD is a data-quality gate and does **not** require the crosswalk's cross-source completeness metric to be 100% (that's tracked separately). Every threshold was calibrated against real measured data, not guessed (e.g. ESPN/FFC ADP agreement is round-sensitive: near-perfect agreement in the top ~50 picks, real market noise beyond pick 100).
- Agent access layer (§7, `access/`): all 8 tool functions from the design exist with the correct signature (`get_player_stats`, `get_adp`, `get_team_context`, `get_depth_chart`, `get_vacated_opportunity`, `get_comps`, `get_league_scoring`, `list_data_gaps`), pinned to the latest GOLD snapshot by default (`access/snapshot_resolver.py` refuses to serve anything that isn't GOLD-marked) and returning a `{values, source, snapshot_date, schema_version, available, note}` citation payload. `get_player_stats` reads real nflverse weekly stats (season/last4/last8 windows; sums counting stats and EPA, averages rate stats like target_share/wopr/racr). `get_team_context` reads real team_stats (plays/game, pass rate, EPA/game) but reports 3 specific sub-fields (`proe`, `win_total`, `ol_rank`) as genuinely unavailable — none are computable from any free source (PROE needs a play-calling expectation model; Vegas win totals are a betting-market product; OL rank isn't a raw stat). Only `get_vacated_opportunity` remains a full stub, needing pbp/roster history to detect departed players. `get_comps` is real but scoped to bio/draft-capital similarity only (no production/efficiency stats joined in yet).
- Not yet built: the curated layer beyond the crosswalk itself, the data dictionary (§5) — largely meaningful now, but share-based metrics (carry_share, snap_share, route_participation) still need snap-count data (`load_snap_counts` unused so far) — and nflverse pbp/rosters/depth-charts/draft-combine.

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
- **D-007 (deviation from Phase 1 design):** Sleeper's real API has no ADP endpoint. FantasyFootballCalculator's free API fills that role instead.
- **D-008 (implementation detail, not a design deviation):** ESPN's actual working API host is `lm-api-reads.fantasy.espn.com` — `fantasy.espn.com` is WAF-blocked for plain HTTP clients.
