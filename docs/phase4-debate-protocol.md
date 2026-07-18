# Phase 4 — Debate Protocol & Judge Prompt
## Structured Adjudicated Debate for the Five-Analyst Ensemble

---

## Design Notes (how the research shaped this)

**From multi-agent debate research:**
- Debate only improves accuracy when participants bring genuinely different information/methods — which Phase 3 guarantees. Agents receiving identical inputs gain nothing from debating.
- Exchanging **full reasoning chains** substantially outperforms exchanging final answers only. Every debate message below carries evidence and reasoning, never just a rank.
- **Adjudicated** debate (judge merges positions) outperformed baselines in 20 of 21 tested settings; but more rounds and more agents do NOT reliably help — returns flatten fast, and costs compound because every message enters every agent's context. Hence: hard 2-round cap, debate only the disagreements.
- Sycophantic convergence is the known failure mode: agents folding to confident-sounding arguments. Countermeasures here: pre-registered positions (from Phase 3), a rule that position changes must cite the specific evidence that moved them, and a judge that penalizes uncited flips.

**From LLM-as-judge research — the judge is the weak point, so it gets the most engineering:**
- Documented judge biases: **position bias** (favoring whichever argument appears first — even strong judges still flip ~15% of verdicts when order is reversed), **verbosity bias** (longer arguments win regardless of quality — often worse than position bias), **self-preference/family bias** (judges favor outputs from their own model family), **sycophancy toward unverified quotes**, and **overconfidence**.
- Mitigations adopted below: randomize presentation order and evaluate contested players in both orderings where feasible; explicit rubric instruction that length is not evidence; judge runs on a different model/config than the debaters; uncited claims are struck before judging; vague rubrics produce noise, so the rubric is concrete and versioned.
- Judge scoring must be **calibrated against a reference**: Phase 4 validation includes a position-flip audit and comparison of judge verdicts against frozen-world ground truth. Rubric text is version-tagged — an unversioned rubric tweak that changes scores is a regression you can't bisect.

**Architecture principle (from Phase 3 carried forward):** the debate is a deterministic *workflow* — agenda selection, turn order, and round count are code, not agent choices. Only the content of arguments is model-generated.

---

## 1. Debate Protocol Specification

### 1.1 Inputs
- Five schema-valid ranking sets (Phase 3 output) for one position, same snapshot date
- Each agent's backtested positional accuracy weights (from WBS task 3.4)
- Prompt/rubric version tags for the run log

### 1.2 Agenda selection (code, not agents)
1. Compute pairwise rank deltas across the five sets for every player.
2. A player enters the agenda if: max rank spread ≥ 5 (top-24 players) or ≥ 8 (later players), OR any agent's `confidence ≥ 0.8` position differs from the median by ≥ 4 ranks.
3. Cap the agenda at the top 8-12 contested players per position (budget from Phase 0). Consensus players skip debate entirely and pass straight to the judge for mechanical merging.

### 1.3 Round structure (hard cap: 2 debate rounds)
- **Round 0 — Committed positions (already done in Phase 3).** Each agent's rank, rationale, evidence, confidence, and `what_would_change_my_mind` are frozen before any agent sees another's output. These commitments are the debate's anchor.
- **Round 1 — Challenge & defend.** For each agenda player, each agent receives the other four agents' Round 0 entries (full reasoning chains) and must (a) directly address the strongest opposing argument, (b) state whether any opposing evidence meets its own pre-registered falsifier, and (c) hold or revise with cited cause.
- **Round 2 — Final revision.** Agents see all Round 1 messages and submit a final position. Any change from Round 0 must name the specific evidence that triggered it. "Others disagreed with me" is not evidence.
- **Adjudication.** The judge receives the full transcript per agenda player and produces the merged rank with written rationale. Consensus players are merged by accuracy-weighted average without debate.

### 1.4 Debate message schema (all rounds)
```
{
  "agent_id": "",
  "player_id": "",
  "round": 1,
  "current_rank": 0,
  "changed_from_round0": false,
  "change_trigger": "<null, or the specific cited evidence that moved me>",
  "strongest_opposing_argument": "<steelman of the best case against my position>",
  "response_to_opposition": "<why it does/doesn't change my rank, with citations>",
  "falsifier_status": "<does any presented evidence meet my pre-registered
                       what_would_change_my_mind condition? yes/no + explanation>",
  "evidence": [ {"metric": "", "value": "", "source": "", "snapshot_date": ""} ]
}
```

### 1.5 Hygiene rules (enforced by orchestration code)
- Uncited quantitative claims are struck from the transcript before the next round and before judging.
- Messages are length-capped (~250 words of argument per player) so verbosity can't masquerade as strength.
- Turn order within a round is randomized per player.
- Full transcript, prompt versions, and agent weights are archived per run for the Phase 7 postmortem.

---

## 2. Round 1 Debate-Turn Prompt (injected per agenda player)

```
# DEBATE — ROUND 1 (Challenge & Defend)
You are {agent_id}. Your Round 0 position on {player} is attached,
along with the Round 0 positions of the four other analysts, including
their full evidence and reasoning.

Your task for {player}:
1. STEELMAN: Identify the single strongest argument against your
   position and state it fairly — as its author would.
2. RESPOND: Address that argument directly with cited evidence from
   the gold snapshot. Attacking a weaker argument while ignoring the
   strongest one will be penalized by the judge.
3. FALSIFIER CHECK: Compare the opposing evidence against your
   pre-registered "what_would_change_my_mind" condition from Round 0.
   State plainly whether the condition is met.
4. HOLD OR REVISE: Keep or change your rank. A change is valid ONLY
   if you name the specific evidence that triggered it. Changing to
   agree with the majority, or because another analyst sounded
   confident, is explicitly invalid and will lower your ensemble
   weight.

Rules:
- Argue from YOUR methodology. Do not adopt another analyst's
  methodology mid-debate; your value to the ensemble is your
  independent lens.
- Maximum 250 words of argument. Extra length adds no credit.
- Every statistic must carry a citation; uncited claims are struck.
- Being persuaded by evidence that meets your falsifier is good
  analysis, not weakness. Being persuaded by anything else is noise.

Output: one debate message in the DEBATE MESSAGE SCHEMA. JSON only.
```

## 3. Round 2 Revision Prompt

```
# DEBATE — ROUND 2 (Final Position)
You are {agent_id}. Attached: the complete Round 0 and Round 1
transcript for {player}.

Submit your FINAL position. Requirements:
1. If your rank differs from Round 0, "change_trigger" must name the
   specific cited evidence that moved you and why it satisfies (or
   newly created) a falsifying condition for your prior view.
2. If you hold your Round 0 rank, "response_to_opposition" must
   explain why the strongest Round 1 challenge does not meet your
   falsifier.
3. Set your final confidence. It is acceptable — and often correct —
   to hold your rank while lowering confidence.
4. No new debate threads. This round closes the record.

Maximum 150 words of argument. JSON only, DEBATE MESSAGE SCHEMA.
```

---

## 4. Judge Agent System Prompt

**Deployment notes:** Run the judge on a different model or configuration than the debaters (mitigates self-preference/family bias). Presentation order of agent positions is randomized by the orchestration code; for the top contested players (e.g., top-12 board implications), run the judge twice with reversed orderings and reconcile — if the verdict flips with order, mark the player "order-sensitive" and default to the accuracy-weighted average. Rubric below is version-tagged; any edit increments the version and triggers a harness re-run.

```
# ROLE
You are the adjudicating judge for a five-analyst fantasy football
research team. The analysts have completed a structured debate. Your
job is to produce the final ranking for each contested player, with a
written verdict. You are an evaluator, not a sixth analyst: you do not
introduce new evidence, retrieve new data, or apply your own football
opinions. You weigh what is in the record.

# RUBRIC (version: {rubric_version})
Score each analyst's final position on the contested player against
these criteria, in this priority order:

1. EVIDENCE QUALITY (weight: highest)
   - Are claims cited to the gold snapshot? (Uncited claims have
     already been struck; a position that lost its load-bearing
     evidence to strikes is a weak position.)
   - Does the evidence actually support the stated conclusion, at the
     stated strength? Penalize conclusions that outrun their data.
   - Sample size and stability: stable metrics (volume, role) outweigh
     unstable ones (TD rate, small-sample efficiency).

2. ARGUMENTATIVE INTEGRITY
   - Did the analyst steelman and directly address the strongest
     opposing argument, or dodge to a weaker one?
   - Were rank changes triggered by specific cited evidence (credit)
     or by social pressure/majority position (penalize)?
   - Did the analyst honor their pre-registered falsifier — revising
     when it was met, holding when it wasn't? Penalize an analyst
     whose falsifier was clearly met but who held anyway, and an
     analyst who flipped without any falsifying evidence.

3. TRACK RECORD (weight: supplied, not inferred)
   - Apply the attached backtested accuracy weights for this position:
     {agent_accuracy_weights}
   - These weights break ties and scale trust; they never override
     decisively better evidence in the current record.

# EXPLICIT BIAS CONTROLS (binding on you)
- LENGTH IS NOT EVIDENCE. Do not credit an argument for thoroughness,
  detail, or word count. A two-sentence argument with one decisive
  citation beats a long argument with weak citations.
- ORDER IS NOT EVIDENCE. The sequence in which positions appear
  carries no information; it was randomized.
- CONFIDENT TONE IS NOT EVIDENCE. Rhetorical certainty, vivid
  phrasing, and assertive language carry no weight. Only citations
  and logic do.
- CONSENSUS IS NOT EVIDENCE. Four analysts agreeing is not itself a
  reason; evaluate whether their agreement rests on the same single
  piece of evidence (fragile) or independent lines (robust).
- Do not favor reasoning styles resembling your own.

# VERDICT PROCEDURE (per contested player)
1. Summarize the genuine crux of disagreement in 1-2 sentences (what
   would have to be true for each side to be right).
2. Score each analyst against the rubric (0-10 per criterion, shown).
3. Set the final rank. It may match one analyst, sit between
   positions, or in rare cases sit outside the range ONLY if the
   record shows all analysts made the same identified error.
4. Write the verdict rationale: which evidence was decisive and why.
5. Assign final confidence (0-1) reflecting record quality, and flag
   "order_sensitive": true if instructed that reversed-order
   adjudication disagreed.
6. Record dissent: if a rejected position had a plausible path to
   being right, note the observable event that would vindicate it
   (this feeds the Phase 5 watch list).

# OUTPUT FORMAT
JSON only:
{
  "player_id": "",
  "crux": "",
  "scores": {"opportunity_analyst": {"evidence": 0, "integrity": 0},
             "efficiency_analyst": {...}, "profile_analyst": {...},
             "market_analyst": {...}, "context_analyst": {...}},
  "final_rank": 0,
  "final_proj_ppg": 0.0,
  "verdict_rationale": "<the decisive evidence and why>",
  "confidence": 0.0,
  "order_sensitive": false,
  "dissent_watch": "<event that would vindicate the losing position,
                    or null>",
  "rubric_version": "{rubric_version}"
}
```

---

## 5. Validation & Decision Gate (from WBS 4.4)

Run the full pipeline (Phase 3 outputs → agenda → 2 rounds → judge) against both frozen worlds, 3+ runs each, and score in the Phase 2 harness:

| Comparison | Question it answers |
|---|---|
| Ensemble vs. best solo agent | Does debate+judging add signal at all? |
| Ensemble vs. accuracy-weighted average (no debate) | Is the debate worth its cost over simple math? |
| Ensemble vs. ADP/ECR baselines | Does the whole system clear the market bar? |

**Judge-specific audits (from LLM-as-judge best practice):**
- **Position-flip audit:** re-adjudicate a sample of contested players with reversed presentation order; flip rate above ~5-10% means the bias controls aren't holding — tighten the rubric or expand dual-order adjudication.
- **Verbosity audit:** check correlation between argument length and rubric scores across the transcript archive; meaningful positive correlation means the length control is failing.
- **Falsifier-integrity audit:** count Round 1-2 rank changes with valid vs. invalid triggers; a high invalid-flip rate means the debaters are converging socially and the debate is theater.
- **Ground-truth calibration:** on frozen worlds, check whether the judge's per-analyst rubric scores correlate with which analyst was actually right — a judge whose "winner" loses to reality more often than the accuracy-weighted average is subtracting value.

**Decision gate (recorded in the decision log):** keep the debate layer only if the ensemble beats both the best solo agent AND the weighted average on frozen worlds. Otherwise ship the weighted average — it is a respectable, far cheaper fallback — and revisit debate next season with a season of new transcript data.

## 6. Cost Controls
- Agenda cap (8-12 players/position) and word caps bound tokens per session.
- Consensus players never enter debate — they are merged mechanically.
- Dual-order adjudication only for top-of-board players where a flip changes draft behavior.
- Log token spend per run against the Phase 0 budget line.
