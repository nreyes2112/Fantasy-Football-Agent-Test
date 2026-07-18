# Decision Log — Multi-Agent Fantasy Research System
*Append-only. Entries are never edited; superseded decisions get a new entry linking back. Format per phase0-charter-design.md Appendix D.*

---

## D-001 | 2026-07-18 | Charter v1.0 approved
Status: ACCEPTED (2026-07-18)
Context: Design complete for all phases; league settings, draft date, milestones, and budget now known and filled into the charter.
Decision: Charter v1.0 adopted — metrics (SM1–SM4), scope (v1 = pre-draft season-long QB/RB/WR/TE for league 94172663), league settings (12-team, 1.0 PPR, 4pt pass TD, ESPN), milestones worked back from Aug 29 draft.
Alternatives rejected: Planning to Aug 30 (rejected: plan to the earlier possible date so a date change adds slack rather than removing it).
Judged by: All of SM1–SM4 in January; O3 gate on Aug 19.
Charter impact: This IS the charter.

## D-002 | 2026-07-18 | Data sources and canonical player ID
Status: ACCEPTED
Context: WBS originally referenced nfl_data_py; that library is deprecated in favor of nflreadpy (nflverse team's maintained Python port). Sources needed for stats, market data, and league context.
Decision: nflreadpy for all nflverse data (stats, pbp, rosters, depth charts, draft/combine, schedules). Canonical player ID = nflverse gsis_id with a weekly-rebuilt crosswalk; nfl_data_py retained only as emergency fallback for import_ids() crosswalk. Market sources per D-005.
Alternatives rejected: nfl_data_py as primary (deprecated, unmaintained); name-based joins as primary matching (corruption vector — proposals-only, human-confirmed queue per Phase 1 §3).
Judged by: Phase 1 exit checklist — 100% charter-universe crosswalk coverage; SM4 citation integrity.
Charter impact: none.

## D-003 | 2026-07-18 | Frozen-world backtest dates
Status: ACCEPTED
Context: Backtest harness needs point-in-time worlds matching the real July decision point.
Decision: Two initial frozen worlds: 2024-07-15 and 2025-07-15, assembled per phase2 spec with leakage audits. Each completed season is archived as a new world (walk-forward growth).
Alternatives rejected: Single-world testing (single-season overfitting); shuffled cross-validation (destroys temporal ordering — invalid for this problem).
Judged by: Leakage audit checklists pass; O3 requires beating baselines on BOTH worlds.
Charter impact: none.

## D-004 | 2026-07-18 | Five-agent methodology split
Status: ACCEPTED
Context: Debate research shows gains come from genuine methodological diversity, not personas.
Decision: Five agents differentiated by methodology and data emphasis: opportunity/volume, efficiency (regression-mandatory), profile/comps, market/ADP, team-context (top-down). Subject to WBS 3.4 kill/merge if any agent adds no unique signal. Build order: Agent 1 (opportunity) validated end-to-end first, others templated from it.
Alternatives rejected: Persona-differentiated agents (no expected accuracy gain per debate research); building all five simultaneously (violates measure-then-iterate guidance).
Judged by: WBS 3.4 solo validation scorecards; Phase 7 Workstream C attribution.
Charter impact: none.

## D-005 | 2026-07-18 | ESPN as league platform and primary market signal
Status: ACCEPTED
Context: DEVIATION from Phase 1 design, which assumed Sleeper as the league platform. Nick's league is on ESPN (ID 94172663, private). The market baseline that matters is the one the actual draft room prices from.
Decision: ESPN unofficial API becomes (a) the league-settings source of truth — Phase 1 reader pulls settings and diffs against charter §5 — and (b) the PRIMARY ADP source for the board's DELTA column and My Guys pricing. Sleeper ADP demoted to secondary/backup market source (simultaneously satisfying risk R2's backup-ADP requirement). FantasyPros ECR unchanged as expert-consensus source. Private-league auth via SWID/espn_s2 cookies, local-only, never committed.
Alternatives rejected: Sleeper ADP as primary (measures a different drafter population than the actual ESPN room); manual transcription of league settings (unverified §5 is a known failure mode the design explicitly warns against).
Judged by: SM1 — My Guys ADP-implied finishes are priced off the ADP the real room uses; Phase 1 exit checklist (settings diff clean, backup source test-pulled).
Charter impact: §5 platform fields; §10 R2 backup source named.

## D-006 | 2026-07-18 | Zero-API-spend execution model
Status: ACCEPTED
Context: DEVIATION from Phase 0 design's per-month API budget line. Nick requires $0 incremental spend; everything runs within the existing Claude plan.
Decision: Agent runs, debates, judging, and backtests execute inside Claude Code sessions under the Claude plan (semi-manual kickoff accepted). The daily capture job — the only component that must run unattended — is a small Python script on GitHub Actions free-tier cron, independent of any personal machine. Budget controls become plan-usage discipline: agenda caps, round caps, word caps per Phase 4 design. Capture priority over analysis if limits are hit.
Alternatives rejected: Metered Claude API for agents (violates the $0 constraint); running capture on a local machine's cron (fails when the laptop sleeps — un-backfillable data at risk, R2).
Judged by: SM4 — capture uptime ≥ 90%; M5/M7 hit on schedule despite semi-manual runs.
Charter impact: §7 rewritten (spend ceiling $0; usage discipline replaces token budget).

## D-007 | 2026-07-18 | Sleeper has no ADP endpoint; FantasyFootballCalculator adopted as the free ADP source
Status: ACCEPTED
Context: DEVIATION from phase1-data-platform-design.md §2, which lists ADP under "Sleeper API." Verified against Sleeper's official docs (docs.sleeper.com) and live calls while building the Tier 1 capture job: Sleeper's public API exposes only the full player list (meta + injury status, `/players/nfl`) and trending adds/drops (`/players/nfl/trending/{add,drop}`) — there is no ADP endpoint, documented or otherwise. This also affects charter §10 R2, which names Sleeper as the backup ADP source.
Decision: FantasyFootballCalculator's free, keyless REST API (`fantasyfootballcalculator.com/api/v1/adp/{scoring}?teams=&year=`) is adopted as the ADP source for the daily capture job, scoped to this league's format (12-team, PPR). Sleeper's daily pull is retained for exactly what it actually provides: player meta, injury status, and trending adds/drops. FantasyPros ECR remains the expert-consensus source (D-005); ESPN remains the intended PRIMARY market signal once the ESPN reader is built (D-005) — this decision only fills the free-secondary-source gap ahead of that.
Alternatives rejected: fabricating or hand-transcribing ADP (violates the project's no-uncited-data rule); scraping FantasyPros' ADP page directly for this role (ECR is FantasyPros' job per D-005; mixing sources muddies which one is being measured against); leaving ADP capture blank until the ESPN reader ships (rejected — ADP is the un-backfillable dataset per Phase 1 design notes; every day without a capture job running is unrecoverable history).
Judged by: Phase 1 exit checklist (backup ADP source named and test-pulled — this satisfies it with FFC instead of Sleeper); SM4 capture uptime.
Charter impact: §10 R2 backup-ADP-source name should be read as FantasyFootballCalculator, not Sleeper, until/unless charter.md is edited to match.

## D-008 | 2026-07-18 | ESPN API host: use lm-api-reads, not fantasy.espn.com
Status: ACCEPTED
Context: Building the ESPN reader (phase1-data-platform-design.md §2, charter §5's closing check). `https://fantasy.espn.com/apis/v3/...` — the host every existing ESPN-API guide and library (including the community `espn-api` project) documents — now sits behind an AWS WAF challenge: requests return `202` with an empty body and header `x-amzn-waf-action: challenge`, which a plain HTTP client cannot solve (it requires executing JS, as with a browser bot-check). This is newer than most public documentation of ESPN's API and would have silently blocked the reader if not caught.
Decision: Use `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{leagueId}` instead — same path structure, same `view` query params (`mSettings`, `kona_player_info`), no WAF challenge encountered in testing. Verified live against league 94172663: `mSettings` returned full settings (all charter §5 fields matched — see the same-day settings-diff run), and `kona_player_info` with an `x-fantasy-filter` header returned a 600-player pool with real `ownership.averageDraftPosition` values consistent with FantasyFootballCalculator's numbers for the same players. A browser-like `User-Agent` header is sent defensively on all ESPN requests in case `lm-api-reads` ever grows the same WAF layer.
Alternatives rejected: driving a real browser (Playwright/Selenium) to solve the WAF challenge — large complexity/fragility increase for a problem that a different, equally-official ESPN host already avoids; the third-party `espn-api` PyPI package — hand-rolled requests using verified field paths keep the codebase consistent with the Sleeper/FFC readers and avoid an unverified dependency's version-compatibility risk.
Judged by: Phase 1 exit checklist (agent tool layer / ESPN reader works); every future ESPN pull silently succeeding rather than 202-ing forever.
Charter impact: none (implementation detail), but phase1-data-platform-design.md §2's "ESPN unofficial API" line should be read as `lm-api-reads.fantasy.espn.com`, not `fantasy.espn.com`.
