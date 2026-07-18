# Multi-Agent Fantasy Football Research System
## Detailed Work Breakdown Structure with Research-Backed Best Practices

**Project goal:** Build a data-grounded, multi-agent analysis system that produces position ranks, tiers, an overall draft board, and a falsifiable "My Guys" list that measurably beats market consensus (ADP/ECR).

---

## Research Foundations — Cross-Cutting Lessons Learned

Before the phase breakdowns, these are the lessons that apply to the whole project, drawn from production case studies and published research:

1. **Simple, composable patterns beat complex frameworks.** Anthropic's engineering guidance (from working with dozens of teams building LLM agents) is consistent: the most successful implementations use simple workflows — prompt chaining, routing, orchestrator-worker — not elaborate agent frameworks. Use *workflows* (predefined steps) wherever the process is deterministic; reserve true *agent* autonomy only where open-ended reasoning is essential. Most of this project is a workflow.

2. **An agent only knows what's in its context window.** When an agent makes a weird decision, it almost always lacked context. Design every prompt by simulating the task from the agent's perspective: what data does it actually see? Budget context deliberately.

3. **Ground every stat in tools, never model recall.** The NFL/AWS production fantasy assistant succeeded by wiring agents to real NextGen Stats data via MCP and holding output to analyst review (90% approval bar). LLMs recall plausible-but-wrong stats confidently; a single hallucinated target share poisons a debate.

4. **Opportunity predicts; efficiency regresses.** Published fantasy modeling work consistently finds volume/opportunity metrics more predictive year-over-year than efficiency metrics, and that strong systems model *team-level* output first, then allocate to players (similarity-score / comp-based approaches against decades of historical data).

5. **Backtests are only valid if leakage-free and broken out by position.** Aggregate error metrics can look great while a single position (historically TE) fails systematically. Any backtest with future data leakage, or filtered to only high-scoring players (survivorship bias), is invalid.

6. **Multi-agent debate only adds signal when agents are genuinely diverse.** Research shows debate between agents with identical inputs does not improve expected correctness; gains come from diversity of information/methodology, from sharing full reasoning chains (not just answers), and from adjudicated judging. More agents and more rounds do NOT reliably help — 2-3 rounds is the sweet spot.

7. **Build the eval harness before the thing being evaluated.** Eval-driven development: a golden dataset + automated scoring is what separates measured progress from vibes. Cheap heuristic checks first; LLM-as-judge only where needed, calibrated against human review; track trends, not absolute scores.

---

## Phase 0 — Project Charter & Success Definition
**Duration:** 2-4 days | **Dependency:** none

### Best practices & lessons learned
- Teams that skip measurable success criteria end up with "vibes-based quality" — no way to know if the system works or if a change helped. Every downstream decision (keep the debate layer? drop an agent?) must resolve against a number defined here.
- Scope discipline was the difference-maker in the NFL/AWS build (concept to production in 8 weeks): ruthlessly cut anything not serving the core deliverable.
- Write decisions down. A one-page decision log prevents re-litigating settled questions mid-project.

### Work breakdown
- **0.1 Define success metrics (quantitative, scoreable in January)**
  - Primary: "My Guys" hit rate — % who outperform their ADP-implied positional finish (target: >60%)
  - Secondary: Spearman rank correlation of final board vs. actual end-of-season value, compared against ECR and ADP baselines
  - Tertiary: tier accuracy — % of players finishing within their assigned tier ±1
- **0.2 Lock league constraints as system inputs**
  - Scoring settings (PPR/half/superflex/TE premium), roster construction, dynasty vs. redraft context, keeper rules
  - Dynasty adjustment policy: how age/contract/long-term value weight into ranks vs. pure season projection
- **0.3 Define scope guardrails**
  - In scope: pre-draft season-long rankings. Out of scope (v1): weekly lineup advice, DFS, trade valuation
- **0.4 Set budget & operating limits**
  - API cost ceiling per month; compute time per daily task; total agent tokens per debate session
- **0.5 Create the decision log**
  - Single markdown file; every architectural decision gets one line: date, decision, reason, metric it will be judged by
- **0.6 Risk register (initial)**
  - Data source shutdown/rate limits, hallucinated stats, agent convergence/groupthink, calendar risk (draft date is a hard deadline)

### Exit criteria
Charter doc exists with numeric success criteria, locked league settings, and a scored baseline target (what did ADP/ECR alone achieve last year?).

---

## Phase 1 — Data Platform
**Duration:** 1-2 weeks | **Dependency:** Phase 0

### Best practices & lessons learned
- **Point-in-time correctness is everything.** Backtests die on leakage: the July 2025 snapshot must contain only what was knowable in July 2025. Historical ADP is hard to reconstruct after the fact — start snapshotting NOW, daily/weekly, even before the rest of the system exists.
- Production fantasy systems (Yahoo, BetIQ/TeamRankings) blend multiple sources rather than trusting one; they also model team-level context (Vegas win totals, coaching, depth charts) before player-level stats.
- One shared, versioned dataset for all agents. If agents fetch their own data ad hoc, debates become arguments about whose numbers are right instead of what the numbers mean.
- Validation at ingestion: schema checks, range checks (no negative snap counts), completeness checks (every rostered fantasy-relevant player present).

### Work breakdown
- **1.1 Source selection & access**
  - Historical stats: nfl_data_py / nflverse (play-by-play, weekly, seasonal, snap counts, depth charts)
  - Market data: Sleeper API + FantasyPros ADP/ECR (snapshot on schedule)
  - Team context: Vegas win totals, offensive line rankings, coaching/coordinator changes
  - News/injury: structured feed selection (Sleeper trending, official injury reports)
- **1.2 Schema & data dictionary**
  - Canonical player ID mapping across sources (the unglamorous task that breaks everything if skipped)
  - Define every derived metric once (e.g., YPRR, target share, weighted opportunity) with formula documented
- **1.3 Snapshot & versioning system**
  - Dated, immutable snapshots (e.g., `data/snapshots/2026-07-17/`); no in-place updates
  - ADP snapshot job runs from day 1 of the project
- **1.4 Validation pipeline**
  - Automated checks on every ingest; failed checks block the snapshot from being marked "gold"
- **1.5 Access layer for agents**
  - Query tools/functions agents call to retrieve stats — the ONLY sanctioned path to numbers
  - Tool responses include source + snapshot date so citations are automatic

### Exit criteria
A gold snapshot exists; an agent can answer "what was Player X's target share in 2025?" via tool call with citation; ADP snapshots accumulating on schedule.

---

## Phase 2 — Backtest Harness
**Duration:** 1-2 weeks | **Dependency:** Phase 1

### Best practices & lessons learned
- Build the grader before the players. Eval-first development means every subsequent phase ships with a score attached instead of an opinion.
- Invalid backtests (from published projection-validation work): any future-data leakage, any filtering to players above a points threshold (survivorship bias), any aggregate-only error reporting. **Always break out by position and scoring format** — aggregate MAE hides systematic positional failure (TE is the perennial worst).
- Compare against *naive baselines*, not just each other: last-year's-points-repeated, raw ADP order, and ECR. A system that can't beat "just use ADP" retroactively should not override ADP prospectively.
- Run each configuration multiple times (LLM outputs vary); report mean and variance. High variance is itself a red flag worth investigating.

### Work breakdown
- **2.1 Frozen-world test sets**
  - Reconstruct July 2024 and July 2025 knowledge states (stats through prior season, that summer's ADP, that summer's depth charts)
  - Document exactly what each frozen world contains; leakage audit checklist
- **2.2 Scoring engine**
  - Metrics: MAE of projected points (per position), Spearman rank correlation vs. actual finish, top-24/top-36 hit rates, "My Guys" simulated hit rate
  - All metrics computed per-position and per-scoring-format, plus overall
- **2.3 Baseline bank**
  - Score ADP-order, ECR, and naive last-year-points against both frozen worlds; record as the bar to clear
- **2.4 Runner & report template**
  - One command runs any candidate system against a frozen world and emits a standard scorecard
  - Variance protocol: 3 runs minimum per configuration, report mean ± spread
- **2.5 Regression tracking**
  - Every scorecard archived with config hash; trend view across iterations

### Exit criteria
Baselines scored and documented. Any candidate ranking system can be evaluated with one command and compared against ADP/ECR on identical frozen data.

---

## Phase 3 — Agent Development
**Duration:** 2-3 weeks | **Dependency:** Phase 2

### Best practices & lessons learned
- Anthropic's core guidance: start with a single simple agent, measure, iterate — don't build all five simultaneously. Get one analyst working end-to-end, validate it in the backtest harness, then template the others.
- Differentiate agents by **methodology and data emphasis, not persona**. Debate research is clear: identical-input agents debating adds no expected accuracy. Each agent should be capable of being genuinely right when the others are wrong.
- Structured outputs (JSON schema: player, projection, confidence, cited evidence) — not prose. Prose rankings can't be scored, merged, or debated systematically.
- Every claim must carry a tool-sourced citation. An uncited stat is treated as invalid in debate — this single rule is the main hallucination defense.
- Keep each agent a *workflow* where possible: fetch data → compute → rank → justify. Reserve open-ended reasoning for the justification step only.

### Work breakdown
- **3.1 Agent architecture template**
  - Shared skeleton: role definition, data-access tools, output JSON schema, citation requirement, confidence calibration instructions
- **3.2 Build Agent 1 (Opportunity/Volume analyst) end-to-end**
  - Emphasis: target share, snap share, weighted opportunity, red-zone usage, depth chart position
  - Backtest solo against both frozen worlds; iterate prompt until it beats naive baseline
- **3.3 Template remaining agents from validated skeleton**
  - Agent 2 — Efficiency analyst: YPRR, EPA, yards after contact, success rate (with regression-to-mean priors)
  - Agent 3 — Profile/Breakout analyst: age curves, draft capital, career-arc comps, similarity scoring
  - Agent 4 — Market analyst: ADP movement, ECR deltas, where market disagrees with underlying data
  - Agent 5 — Context analyst: team-level projection first (Vegas totals, O-line, coaching change), then player allocation
- **3.4 Solo validation round**
  - Score all five independently in the harness; document each agent's strengths/blind spots by position
  - Kill/merge criterion: an agent that never adds unique signal gets merged or cut — five is a design choice, not a requirement
- **3.5 Confidence calibration check**
  - Verify stated confidence correlates with backtest accuracy; recalibrate prompt language if agents are systematically overconfident

### Exit criteria
Each surviving agent produces schema-valid, fully-cited rankings and has a documented backtest scorecard vs. baselines.

---

## Phase 4 — Debate & Adjudication Layer
**Duration:** 1-2 weeks | **Dependency:** Phase 3

### Best practices & lessons learned
- Debate research findings that shape this design: (a) sharing **full reasoning chains** substantially outperforms sharing final answers only; (b) **adjudicated** debate (a judge model merging positions) outperformed single-model baselines in 20 of 21 tested settings; (c) throwing more rounds/agents at debate does not reliably improve accuracy — returns flatten fast; (d) role-played personas alone don't create real diversity.
- Watch for sycophantic convergence: agents politely folding to the first confident argument. Countermeasure: require each agent to state what evidence would change its mind *before* seeing others' arguments, and require the judge to penalize position changes not tied to cited evidence.
- The debate layer must justify its existence in the harness. If ensemble output doesn't beat the best solo agent on frozen worlds, ship the solo agent.

### Work breakdown
- **4.1 Disagreement detection**
  - Automated diff of the five ranking sets; surface the top-N largest rank deltas per position as debate agenda (don't debate consensus players — waste of tokens)
- **4.2 Debate protocol design**
  - Round 0: each agent independently commits rankings + "what would change my mind" statements
  - Round 1: agents exchange full reasoning chains on agenda players; must directly address the strongest opposing evidence
  - Round 2: revision round — position changes must cite the specific evidence that moved them
  - Hard cap at 2-3 rounds
- **4.3 Judge agent**
  - Separate model/prompt from the debaters; merges positions into final ranks with written rationale per contested player
  - Judge instructions: weight arguments by cited-evidence quality and each agent's backtested positional accuracy (from 3.4), not by rhetorical confidence
- **4.4 Ensemble validation**
  - Run full debate pipeline against both frozen worlds, 3+ runs each
  - Compare: ensemble vs. best solo agent vs. simple average of five agents vs. baselines
  - Decision gate: keep debate only if it wins; simple averaging is a respectable fallback and much cheaper
- **4.5 Cost/latency profiling**
  - Token cost per full debate session; trim agenda size or rounds to fit Phase 0 budget

### Exit criteria
A scored decision in the log: debate layer retained/modified/replaced, backed by frozen-world scorecards.

---

## Phase 5 — Ops Layer (Daily Monitoring)
**Duration:** 3-5 days to build; runs continuously | **Dependency:** Phases 1, 4

### Best practices & lessons learned
- The original "agents study daily for a month" concept fails because static data re-analyzed daily yields identical conclusions. The daily task earns its keep only on *deltas*: what changed since yesterday that should move a ranking?
- Memory hygiene: append-only prose memory files degrade into noise. Use a structured changelog (date, player, event, source, ranking impact assessment) that stays queryable.
- Trigger-based re-ranking beats scheduled re-ranking: define what magnitude of event re-opens a player's rank (injury designation, depth chart change, ADP move > X spots) instead of recomputing everything nightly.
- Alert fatigue is real — tune thresholds so the daily brief is 5 lines you read, not 50 you skip.

### Work breakdown
- **5.1 Delta detection job (the daily scheduled task)**
  - Pull today's snapshot; diff vs. yesterday: ADP movement beyond threshold, injury report changes, depth chart changes, notable news
- **5.2 Structured changelog**
  - One record per event: date | player | event type | source | assessed impact (none/watch/re-rank)
- **5.3 Re-rank trigger rules**
  - Codify which events re-open a rank and which agent(s) re-evaluate that player (usually 1 agent + judge, not a full debate)
- **5.4 Daily brief output**
  - Short digest: events detected, triggers fired, ranks changed and why
- **5.5 Ops reliability**
  - Failure alerting (missed snapshot, API error), retry logic, weekly spot-check that changelog matches reality

### Exit criteria
Task runs unattended for 7 consecutive days, correctly flags at least one real re-rank trigger, and produces a brief you actually read.

---

## Phase 6 — Deliverables (Draft Season)
**Duration:** 1 week to produce; maintained until draft | **Dependency:** Phases 4, 5

### Best practices & lessons learned
- Tiers should come from projection *gaps*, not fixed bucket sizes — a tier break is where the drop-off between adjacent players exceeds a threshold (cliff detection), which is also how you make draft-day decisions ("last player in tier" logic).
- "My Guys" published without falsifiable theses are just favorites. Each one needs: projection, ADP, the specific data-backed reason for the gap, and a pre-registered "what would change my mind" trigger. This is what makes Phase 7 scoring possible — and what separates conviction from stubbornness.
- Value is relative to market: the board's key column is projection-vs-ADP delta, because that's the only thing you can act on in a draft.

### Work breakdown
- **6.1 Position rankings** — final judged ranks per position with per-player rationale summary
- **6.2 Tier construction** — gap-based clustering per position; document the threshold used
- **6.3 Overall board** — cross-position value board with ADP, projection, delta, and tier
- **6.4 My Guys list**
  - Selection rule (e.g., delta > threshold AND multi-agent agreement AND confidence above floor)
  - Per player: thesis, cited evidence, ADP cost of the reach, pre-registered invalidation trigger
- **6.5 Draft-day format** — one-page cheat sheet; tier-break emphasis; My Guys flagged with max-reach round
- **6.6 Freeze & version** — final pre-draft board snapshotted for Phase 7 scoring

### Exit criteria
Frozen, versioned board + My Guys list with pre-registered theses, delivered before your draft date.

---

## Phase 7 — Postmortem (January)
**Duration:** 1 week | **Dependency:** season completion

### Best practices & lessons learned
- This is an RCA on your own system — the phase everyone skips, and the reason most people's process never improves. The frozen Phase 6 board plus pre-registered theses make it objective instead of memory-flattering.
- Distinguish "right for the stated reason" from "right by luck": a My Guy who hit because of an injury to a teammate did not validate the thesis. Score the *reason*, not just the outcome.
- Small sample humility: one season of My Guys is ~5-10 data points. Look for process errors (data gaps, systematic positional bias, one agent dragging the ensemble) more than outcome noise.

### Work breakdown
- **7.1 Score against Phase 0 criteria** — My Guys hit rate, rank correlation vs. ADP/ECR baselines, tier accuracy; per-position breakdown
- **7.2 Agent attribution** — which agent's solo ranks were most accurate; where did the judge overrule the eventually-correct agent
- **7.3 Debate value audit** — final ensemble vs. what best solo agent would have produced
- **7.4 Thesis validation** — each My Guy: hit/miss, and was the stated mechanism the actual reason
- **7.5 Root cause analysis of worst misses** — top 5 ranking failures: data gap, methodology flaw, or irreducible variance?
- **7.6 Next-season updates** — adjust agent weights/judge instructions from attribution data; update decision log; archive season as a new frozen world for future backtesting

### Exit criteria
Written postmortem; updated system weights; this season archived as backtest data — the system compounds year over year even though the agents don't.

---

## Appendix A — Suggested Stack
- **Data:** Python, nfl_data_py/nflverse, Sleeper API, scheduled snapshot jobs
- **Agents:** Claude API with tool use; structured JSON outputs; agents as prompted workflows, not framework-heavy autonomy
- **Harness:** plain Python scoring scripts + archived scorecards (a notebook nobody re-runs is not a harness)
- **Ops:** scheduled task (cron / Claude scheduled task) + structured changelog file

## Appendix B — Top Project Risks
| Risk | Mitigation |
|---|---|
| Hallucinated stats enter debate | Tool-only data rule; uncited claims invalid |
| Agents converge (fake debate) | Methodology-differentiated inputs; pre-registered positions; judge penalizes uncited flips |
| Backtest leakage inflates confidence | Frozen-world audits; snapshot immutability |
| Calendar risk (draft date) | Phases 0-2 are the critical path; cut Phase 4 to simple averaging if behind |
| Cost overrun | Debate agenda limited to top disagreements; round cap; budget in charter |
