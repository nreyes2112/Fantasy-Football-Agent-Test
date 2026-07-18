# Phase 6 — Draft Deliverables Design
## Position Ranks, Tiers, Overall Board, My Guys, and the Draft-Day Sheet

---

## Design Notes (how the research shaped this)

- **Tiers are a decision technology, not a presentation choice.** The now-classic approach (Boris Chen's NYT work) clusters expert rankings with a Gaussian mixture model to find natural tiers, and its payoff is the decision rule: favor players in higher tiers, and treat players within a tier as roughly equivalent. That rule kills the two classic draft-clock errors — agonizing between near-equal players, and missing that a cliff is one pick away.
- **Cluster on more than the point estimate.** A known weakness of tiering on projections alone is that it ignores dimensions the projection can't capture; extensions cluster on projection plus ranking/uncertainty together. Our version: tier on projected PPG *and* the system's confidence/disagreement signals, which we uniquely have per player from the debate record.
- **Cross-position value requires a replacement baseline.** Value-based drafting's premise: a player's draft value is his projection minus a positional replacement baseline, ranked across positions by that surplus — the classic finding (the oft-cited Harvard study) being that the winning style drafts by value over replacement while filling starting lineup slots before bench. Crucially, the baseline choice (VOLS vs. VORP vs. blends) materially changes the board, and baselines are league-specific — best derived from how YOUR league historically drafts, not generic tables.
- **Value cliffs are draft-day intelligence.** Practitioners track exactly where the big VOR drop-offs sit (e.g., a 30+ point drop after a specific RB) against how tightly ADP packs those players — that gap between value cliff and ADP cliff is where picks are won.
- **The sheet must survive a pick clock.** Every design choice below serves a 60-90 second decision under pressure: tier breaks visually dominant, one action cue per situation, nothing requiring reading a paragraph mid-draft.

---

## 1. Deliverable D1 — Position Rankings (QB / RB / WR / TE)

Per player row (generated from the Phase 4 judged output):
- Final rank, projected PPG + season total, confidence (judge's final)
- One-line rationale (compressed from the verdict; the full record stays linked, not printed)
- Flags: `MY_GUY`, `DISSENT_WATCH` (a losing debate position with a live vindication path), `ORDER_SENSITIVE` (judge verdict was presentation-order dependent — treat rank as soft), `DATA_GAP`
- Provenance footer per page: snapshot date, prompt/rubric versions, config hash

## 2. Deliverable D2 — Tier Construction

**Method (per position):**
1. Feature per player: judged projected PPG (primary), plus an uncertainty feature = blend of judge confidence and cross-agent rank spread from the debate record (our substitute for the "rank + projection" dual-dimension extension).
2. Fit a 1-2 dimensional Gaussian mixture over candidate tier counts; select count by BIC (the standard model-selection approach), with a sanity band (QB/TE: 5-8 tiers; RB/WR: 7-10).
3. Post-process with a minimum-gap rule: adjacent tiers must be separated by a projected-PPG gap ≥ a configured floor; merge tiers that violate it (prevents BIC from inventing distinctions no drafter can act on).
4. Boundary audit: any player within epsilon of a tier boundary gets a `BOUNDARY` flag — tier membership is a modeling artifact at the margin, and the sheet shouldn't pretend otherwise.

**Printed decision rules (on the sheet itself, verbatim):**
- Prefer the higher tier; within a tier, players are approximately equivalent — use My Guys flags, roster construction, or stacking preference as the tiebreaker, not rank order.
- The number to watch is players-remaining-in-tier, not rank.

**Validation:** tier sets are scored by SM3 (±1 tier accuracy) in the harness before freeze; the tier-count and gap-floor config that wins on frozen worlds is the one that ships.

## 3. Deliverable D3 — Overall Board (cross-position)

**Value translation:**
- `VALUE = judged projected season points − replacement baseline points (position)`
- Baseline: hybrid VOLS-leaning definition — last-starter level adjusted for FLEX deployment, computed from the Charter roster math (teams × starters, FLEX-skewed) AND calibrated against your league's actual historical draft behavior (how many RB/WR actually go in the first N rounds in THIS league). Baseline choice materially moves the board, so the chosen definition + numbers get a decision-log entry.
- Rank all positions together by VALUE. Superflex note: if the Charter says superflex, QB baselines shift dramatically — the roster math handles it, but sanity-check against superflex ADP before trusting.

**Board columns:** overall rank | player | pos | tier | VALUE | current ADP | **DELTA (ADP − board rank)** | flags
- DELTA is the action column: strong positive = the board says take him earlier than the room will; strong negative = let the room reach, not you.
- **Cliff report** (printed beside the board): for each position, where the big VALUE drop-offs sit vs. how tightly ADP packs those same players — each gap annotated with the round where it becomes actionable.

## 4. Deliverable D4 — My Guys List

**Selection rule (mechanical, from Charter Appendix M + Phase 4 output):**
A player qualifies as a My Guy when ALL of:
1. Board-vs-ADP DELTA ≥ threshold (config; e.g., ≥ 12 overall spots or ≥ 1.5 rounds)
2. Judge confidence ≥ 0.7 AND at least 3 of 5 agents rank him above his ADP slot (conviction must be broad, not one agent's crusade)
3. His thesis survived debate (no unresolved falsifier hits in the record)
4. List size stays 5-10 (Charter SM1); if more qualify, take the largest DELTAs

**Per-player thesis card (the contract with January):**
```
MY GUY: {player} ({pos}, Tier {n})
BOARD: #{board_rank}  |  ADP: #{adp}  |  DELTA: +{n}
THESIS: {2-3 sentences, cited — the mechanism, not vibes}
WHY THE MARKET IS WRONG: {info/hype/bias classification from market agent}
MAX REACH: Round {n}, Pick {n} (the price cap — conviction ≠ infinite)
INVALIDATION TRIGGER (pre-registered): {specific observable event}
SCORING LINE (Jan): beats ADP-implied finish of {POS}{n} + startable value
```
The card format IS the pre-registration: thesis, price cap, and kill-switch written before the draft, scored in Phase 7 as stated. "Full-throated" and "falsifiable" coexist — that's the whole point.

## 5. Deliverable D5 — Draft-Day Cheat Sheet

One page (or one screen), optimized for a pick clock:
- **Layout:** per-position tier columns; thick visual breaks BETWEEN tiers (the dominant visual element); thin rules within
- **Per player, one line only:** name, My Guy star / dissent flag, DELTA if |DELTA| ≥ threshold
- **Printed at top:** the two tier decision rules (§2) plus: "Starters before bench" (the value-drafting companion rule: maximize surplus for starting slots before drafting depth)
- **Cliff callouts:** inline markers where a positional cliff is ≤ 6 picks away at typical draft flow ("last of Tier 3 — next WR tier is 20+ points worse")
- **My Guys strip:** the 5-10 names with their max-reach rounds — the only part of D4 that appears on the sheet; thesis cards stay in the appendix for pre-draft reading, not mid-clock reading
- **What's deliberately absent:** bye weeks, strength of schedule, projections to two decimals — anything that invites overthinking inside the clock

Also produced: a live-draft variant (spreadsheet/app-friendly CSV of the board) so players can be struck off and players-remaining-in-tier counts update as the draft runs.

## 6. Freeze & Versioning (Milestone M7)

- Board freeze at draft date − 2 days: D1-D5 generated from the final gold snapshot, stamped with config hash, archived immutable (`/deliverables/2026-final/`)
- Post-freeze events (a preseason injury the night before): handled as annotations ON the frozen board, never regenerations — Phase 7 scores the frozen board, and a silently regenerated board un-registers every thesis
- The freeze bundle includes: all five deliverables, the changelog to date, baseline-bar numbers, and the exact My Guys scoring lines for January

## 7. Phase 6 Exit Checklist
- [ ] Tier config (count bands, gap floor) selected via SM3 on frozen worlds, decision-logged
- [ ] Replacement baselines computed from Charter roster math AND league draft history; decision-logged
- [ ] My Guys selection thresholds configured; produced list is 5-10 with complete thesis cards
- [ ] Cliff report cross-checked against current ADP
- [ ] Cheat sheet passes the clock test: a mock pick decided in < 60 seconds using only the sheet
- [ ] Freeze bundle generated, hashed, archived; January scoring lines written into it
