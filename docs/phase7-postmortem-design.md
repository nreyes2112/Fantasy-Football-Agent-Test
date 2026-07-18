# Phase 7 — Season Postmortem Design
## Scoring, Attribution, Root Cause Analysis, and the Update Loop

---

## Design Notes (how the research shaped this)

- **Blameless, systems-focused framing — adapted for a system of agents.** The SRE postmortem tradition reviews failure to understand how the system failed rather than who to punish, replacing "what went wrong" with "what was missing in our system that allowed this to happen?" Translated here: "the profile agent is bad at TE" is a blame-shaped finding; "the comp database lacks enough TE profiles for stable similarity scoring" is a system-shaped finding you can act on. The distinction isn't kindness to software — system-shaped findings are the only ones that produce fixes.
- **The #1 postmortem trap is resulting.** Judging decision quality by outcome quality — what Annie Duke calls resulting, or outcome bias — is the biggest documented error in decision review: luck can produce good outcomes from bad process and bad outcomes from good process, and the antidote is plotting decisions on a 2×2 of decision quality × outcome quality. This postmortem scores every My Guy and major call on that matrix, because a hit-by-luck teaches the wrong lesson twice.
- **Hindsight bias audit.** Reviewers reliably misremember what was knowable, judging past decisions as if they should have known things they could not have known. Our structural defense already exists: the frozen board, pre-registered theses, and changelog are the record of what was actually known when — the postmortem cites them, never memory.
- **Learning without action isn't learning.** Every finding must convert to a documented improvement — specific action items with owners and due dates are what separate postmortems that work from ones that just feel good. Every finding here terminates in a decision-log entry, a config change, or an explicit "no action, monitor next season."
- **Timely and time-boxed.** Run soon after the season while context is fresh, and keep the exercise short — this design targets a focused week in January, not an offseason of brooding.
- **Small-sample humility throughout:** 5-10 My Guys and one season of deltas is a handful of data points; process errors are diagnosable at that sample size, outcome noise is not.

---

## 1. Inputs (all frozen artifacts — no memory allowed)

Frozen deliverables bundle (Phase 6 M7), including January scoring lines · full-season ground truth under league scoring · debate transcripts + judge verdicts + rubric versions · changelog + Thursday brief archive · all backtest scorecards + decision log · final-season stats snapshot (this becomes the new frozen world).

## 2. Workstream A — Score the Charter (½ day)

Execute Appendix M exactly as written in July:
- SM1 My Guys hit rate (dual condition, per the frozen scoring lines)
- SM2 Spearman vs. actual, system vs. the SAME-DAY frozen ADP/ECR boards, overall + per position
- SM3 tier accuracy (±1), starters weighted
- SM4 process metrics (backtest gate, ops uptime, citation integrity)
Output: one scorecard, PASS/FAIL per criterion, no narrative yet. Numbers first, stories second — the order matters because stories written before numbers bend the numbers.

## 3. Workstream B — Decision-Quality Matrix (the anti-resulting step)

For every My Guy and every top-24 call where the board differed from ADP by ≥ 1 round, classify into the 2×2:

| | Good outcome | Bad outcome |
|---|---|---|
| **Good process** | Earned hit — reinforce | Bad beat — change nothing rashly |
| **Bad process** | Lucky hit — the DANGEROUS quadrant | Earned miss — fix the system |

- "Good process" = thesis mechanism was sound given July's record: cited evidence held, no falsifier was visible, confidence was calibrated.
- The lucky-hit quadrant gets equal scrutiny to earned misses: a My Guy who smashed because the starter ahead of him tore an ACL did NOT validate the thesis, and promoting that pattern is how next season's list degrades.
- Bad beats (good process, bad outcome) explicitly do NOT trigger methodology changes on their own — one season of variance is not evidence against a sound mechanism.

## 4. Workstream C — Attribution (1 day)

- **Per-agent:** each agent's solo July rankings scored against ground truth, per position → next season's judge track-record weights. Also: where did the judge overrule the agent who turned out right, and what did the rubric score that argument?
- **Judge audit:** verdict accuracy vs. the accuracy-weighted average counterfactual; order-sensitive verdicts re-examined; rubric scores correlated with eventual correctness.
- **Debate value:** final ensemble vs. best solo agent vs. no-debate weighted average, all scored on the season — the definitive answer to "did the debate layer earn its cost?", feeding next year's keep/cut decision.
- **Calibration:** stated confidences (agents and judge) vs. realized accuracy in buckets; systematic overconfidence gets a prompt-language fix, not a scolding.

## 5. Workstream D — RCA of the Worst Misses (1-2 days; your home turf)

Select the top 5 ranking failures by impact (biggest value destroyed relative to ADP alternative, not just biggest rank error). For each, run a structured cause analysis against the frozen record:

**Causal taxonomy (every miss lands in exactly one primary bucket):**
1. **Data gap** — the signal existed in the world but not in our snapshot (missing source, stale depth chart, crosswalk error)
2. **Methodology flaw** — the data was present; an agent's lens misread it (e.g., efficiency regression under-shrunk; comp set too thin and unflagged)
3. **Aggregation failure** — an agent got it right; the debate/judge layer lost it (rubric mis-weighting, order sensitivity, invalid social flip)
4. **Ops failure** — the July call was fine; an in-season signal fired and was missed or under-acted (check the changelog: did a trigger fire? was the brief read? did the re-rank happen?)
5. **Irreducible variance** — injury, arrest, scheme change no July process could see; the record shows no missed signal

Discipline rules: causes must cite the frozen record (hindsight-bias defense); "should have known" claims require pointing to the specific July-visible evidence that was ignored; bucket 5 is a legitimate finding, not a failure to find something — force-fitting a "root cause" onto variance creates fake action items that damage a sound process.

## 6. Workstream E — Ops & Changelog Audit (½ day)

Sweep the season's changelog and brief archive: signals caught and acted on (credit the trigger rule) · signals caught but under-acted (threshold or re-rank workflow issue) · signals missed entirely (capture gap or trigger gap — which?) · over-reactions (re-ranks that made the board worse; count them honestly — twitchiness is a failure mode too). Output: per-trigger-class precision/recall-ish tallies → Phase 5 threshold adjustments.

## 7. Workstream F — Actions & Archive (½ day)

- **Update pack, every item as a decision-log entry citing its workstream finding:** new agent accuracy weights (C) · prompt/rubric revisions (C, D) · trigger threshold changes (E) · data source additions (D bucket 1) · debate layer keep/cut/modify (C) · My Guys selection threshold tuning (B) — each with an explicit expectation of what metric it should move next season. No orphan lessons: a finding with no action and no "monitored, no action" tag is an incomplete row.
- **Archive:** final-season snapshot assembled into a new frozen world (leakage audit per Phase 2) — the harness now has three worlds; the walk-forward gets stronger every year, which is how the SYSTEM compounds even though the agents don't.
- **Charter close-out:** next season's charter drafted as v2 with updated baselines (this season's ADP/ECR performance becomes part of the bar).

## 8. Postmortem Document Template (one document, ~4-6 pages)

```
SEASON POSTMORTEM — {year} | Written: {date} | Record basis: frozen bundle {hash}
1. VERDICT vs. CHARTER (the SM1-SM4 scorecard, one table)
2. DECISION-QUALITY MATRIX (all My Guys + major calls plotted; quadrant counts)
3. WHAT THE SYSTEM GOT RIGHT (earned hits + the mechanisms that drove them —
   postmortems that only autopsy failures teach you to stop taking good risks)
4. TOP-5 MISS RCAs (one page: miss, causal bucket, frozen-record evidence, action)
5. ATTRIBUTION SUMMARY (agent table, judge audit, debate verdict, calibration)
6. OPS AUDIT (caught/missed/over-reacted tallies, threshold changes)
7. UPDATE PACK (numbered actions → decision-log IDs → expected metric impact)
8. NEXT-SEASON COMMITMENTS (what v2 charter inherits; what stays frozen for
   comparability; the one thing we will NOT change despite temptation, and why)
```

## 9. Timing & Time-box

- Run window: first two weeks of January (season complete through Week 18; context fresh)
- Total budget: ~5 working days across the workstreams; the time-box is a feature — an over-long review invites narrative-building beyond what one season of data supports
- Sequence is fixed: A (numbers) → B (matrix) → C (attribution) → D (RCA) → E (ops) → F (actions). Scoring before storytelling, always.

## 10. Phase 7 Exit Checklist
- [ ] Charter scorecard complete; every SM has a number and a PASS/FAIL
- [ ] Every My Guy + major call plotted on the decision-quality matrix; lucky hits explicitly labeled
- [ ] Top-5 miss RCAs completed with causal buckets and frozen-record citations
- [ ] Debate layer verdict recorded (keep/cut/modify) with the season's evidence
- [ ] Update pack fully decision-logged; zero orphan lessons
- [ ] New frozen world assembled and leakage-audited; harness now runs 3 worlds
- [ ] Charter v2 drafted; postmortem archived alongside the frozen bundle it scored
