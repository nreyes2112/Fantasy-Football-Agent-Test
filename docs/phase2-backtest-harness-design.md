# Phase 2 — Backtest Harness Design
## Frozen Worlds, Scoring Engine, Baselines, and Regression Tracking

---

## Design Notes (how the research shaped this)

- **Honor the arrow of time.** Standard shuffled cross-validation is invalid for temporal prediction — it destroys temporal ordering and invites leakage; the correct pattern is strict chronological splits where training data corresponds exactly to what was available at the moment of prediction. Our version: fully reconstructed "frozen worlds" — the system sees July 2024 exactly as July 2024 saw itself.
- **Lookahead bias flatters everything.** In quantitative backtesting, overfitting, lookahead bias, and data leakage can make almost any strategy appear profitable in simulation; the defense is treating the pipeline as an engineering audit — strict time ordering and leakage-safe data paths. Hence the leakage audit checklist below is a gate, not a suggestion.
- **Walk-forward as the multi-season pattern.** With two frozen worlds now and a new one added each season (Phase 7 archives), the harness is effectively walk-forward validation — evaluating on each successive out-of-sample season — which is the realistic way to test whether the methodology holds up as regimes change, and to tune for stability rather than one season's peak accuracy.
- **Domain-specific validity rules** (projection-validation literature): a meaningful fantasy backtest must not filter to players above a points threshold (survivorship bias), must not report only aggregate error, and must break out by position and scoring format — aggregate MAE can look impressive while masking systematic failure at one position (TE historically worst).
- **LLM-specific additions** (eval-harness practice): outputs vary run-to-run, so every configuration runs 3+ times with mean and variance reported — high variance is itself a finding; scores are tracked as trends across versions, not worshipped as absolutes; and every scorecard is tied to a config hash so any regression is bisectable.
- **Beat naive baselines or ship the baseline.** The harness's product is not a score — it's a decision: does this configuration override the market, or not?

---

## 1. Harness Architecture

```
 /frozen_worlds/{date}/          candidate system            scorecards/
 (assembled snapshots,     ──▶   (agents / ensemble /   ──▶  {run_id}.json
  pinned via the SAME             baseline) runs with          + trend index
  Phase 1 access layer)           snapshot pinned
```

- **One command:** `run_backtest --system <config> --world <date> --runs 3`
- **Identical code path:** candidates call the Phase 1 agent access layer with the snapshot pinned to the frozen world. No special backtest data path — if production and backtest read differently, the backtest proves nothing.
- **Config hash:** every run records {prompt versions, rubric version, agent weights, metric dictionary version, code commit, world date} → SHA-256 run fingerprint.

## 2. Frozen World Assembly Specification

Two initial worlds: **2024-07-15** and **2025-07-15** (mid-July ≈ your real decision point).

Each world contains, as-of its date:
- Player stats: complete seasons up to and including the PRIOR season only
- ADP/ECR: snapshots dated on/before the world date (source: archived historical ADP; document provenance — reconstructed historical ADP is the highest-leakage-risk item, so record exactly where each number came from)
- Depth charts, rosters, coaching staffs: as of the world date (offseason moves signed before the date included; later moves excluded)
- Draft capital/combine: included for that year's rookie class (draft precedes July)
- Vegas win totals: lines published on/before the world date
- EXCLUDED always: anything timestamped after the world date — including injury outcomes, camp reports, and (critically) any "final 2024 season" aggregates when standing in 2024-07

**Ground truth files** (kept separate from the world, never readable by candidates): actual end-of-season finishes under the league scoring function, PPG with the Charter games threshold, positional ranks.

### Leakage Audit Checklist (run before a world is marked usable, and re-run if its contents ever change)
- [ ] Every file in the world has a source timestamp ≤ world date (automated manifest scan)
- [ ] No column in any curated table is derived from post-date data (trace each derived metric's inputs via the data dictionary)
- [ ] ADP provenance documented per snapshot; no "reconstructed from memory" values
- [ ] Rookie data limited to pre-date events (draft yes, preseason no)
- [ ] Missing values never backfilled from later observations (forward-fill only — backfill is future information)
- [ ] Ground truth stored outside the world directory with access denied to candidate runs
- [ ] Spot check: pick 3 players with known mid-2024/2025 breakout news; confirm the world contains no trace of it
- [ ] Audit result + auditor date recorded in the world's manifest

## 3. Scoring Engine (implements Charter Appendix M — formulas there are binding)

**Headline metrics (per world, per candidate):**
- SM2 Spearman rank correlation vs. actual finish — overall AND per position (QB24/RB48/WR60/TE24 fixed universes)
- SM3 tier accuracy (±1 tier), starters weighted 2x
- Simulated SM1: apply the My Guys selection rule to the candidate's output in the frozen world; score hit rate against that season's ground truth

**Diagnostic metrics (explain the headline, catch pathologies):**
- MAE of projected PPG per position (the aggregate-masking guard)
- Top-12/24/36 hit rates per position
- Calibration curve: stated agent confidence vs. realized accuracy in buckets (feeds WBS 3.5 and the judge's track-record weights)
- Bust avoidance: rate at which candidate ranked eventual busts (finish > 1.5x ADP rank) lower than market did — half the edge in a draft is who you DON'T take
- Positional failure flag: any position whose Spearman trails ADP's by more than the overall gap → named in the scorecard (the TE check)

## 4. Baseline Bank

Scored once per world, archived, and printed on every subsequent scorecard for comparison:

| Baseline | Construction | Why it exists |
|---|---|---|
| ADP-order | Rank = ADP at world date | "The market." The bar the Charter requires beating |
| ECR | Expert consensus at world date | "The experts" — sometimes beats ADP, sometimes doesn't; know which |
| Naive-repeat | Rank = last season's PPG finish | The floor. Anything losing to this is broken |
| Uniform-blend | Simple average of the above | Cheap ensemble sanity check |

Baseline results from BOTH worlds get a decision-log entry (Charter's "Baseline Bar" exercise) — these numbers ARE the operational definition of "beat the market."

## 5. Variance & Significance Protocol

- **3+ runs per configuration** (LLM nondeterminism); report mean ± range for every metric. A configuration whose ranking of candidates flips across runs is unstable — instability is a result, not noise to hide.
- **Bootstrap confidence intervals** on Spearman and hit-rate deltas vs. baselines (resample the player universe, ~2,000 iterations). Report the CI, not just the point delta.
- **Two worlds are two seasons — respect the sample size.** Decision rules:
  - Candidate beats a baseline in BOTH worlds with CI excluding zero in at least one → treat as real
  - Beats in one world, loses in the other → no confident conclusion; prefer the simpler/cheaper configuration
  - Never tune a configuration against a world more than ~3 iterations without checking the other world (single-world overfitting is this project's version of over-optimization)
- **Stability over peak:** when two configurations are within noise, choose the one with lower variance and better positional balance — tuning for robustness rather than one season's peak is the walk-forward lesson.

## 6. Scorecard Schema & Regression Tracking

```
{
  "run_id": "", "config_hash": "", "world": "2024-07-15",
  "system": "ensemble_v3 | opportunity_solo_v2 | baseline_adp | ...",
  "runs": 3,
  "headline": {"spearman_overall": {"mean":0,"range":[0,0]},
               "spearman_by_pos": {...},
               "tier_accuracy": {...},
               "myguys_sim": {"hit_rate":0,"n":0,"picks":[...]}},
  "diagnostics": {"mae_by_pos": {...}, "topN_hits": {...},
                  "calibration": [...], "bust_avoidance": {...},
                  "positional_failure_flags": ["TE"]},
  "vs_baselines": {"adp": {"delta_spearman":0,"ci":[0,0]}, "ecr": {...}},
  "verdict": "PASS/FAIL vs. decision gate in play",
  "notes": ""
}
```

- Archive every scorecard; a small index builds the **trend view**: metric trajectories across config versions per world. Trend beats absolute — the question is always "did this change help?"
- **Gate wiring:** the WBS decision gates read from here — Agent-1 validation (beats naive-repeat), agent kill/merge (unique signal check), the Phase 4 debate gate (ensemble vs. best solo vs. weighted average), and the Charter O3 gate (beats ADP AND ECR both worlds).

## 7. Anti-Pattern Checklist (print at the top of every harness report)

- ✗ Points-threshold filtering of the universe (survivorship bias) — universes are fixed by the Charter
- ✗ Aggregate-only reporting — per-position always
- ✗ Backfilled missing values — forward-fill only
- ✗ Tuning one world repeatedly without cross-checking the other
- ✗ Comparing runs with different metric-dictionary versions as if comparable
- ✗ Reading a 2-season delta as proof — CIs and both-worlds agreement required
- ✗ Any candidate touching ground-truth files (enforced by directory permissions, verified in audit)

## 8. Build Order (1-2 weeks)

1. Frozen-world assembler + leakage audit automation (the audit checklist as code where possible)
2. Ground-truth builder (league scoring function from Phase 1 applied to actual seasons)
3. Scoring engine + scorecard writer (implement Appendix M formulas + diagnostics)
4. Baseline bank runs → decision-log entry (the bar is now set)
5. Runner CLI + config hashing + trend index
6. Dry run: score the naive-repeat baseline as if it were a candidate — verifies the whole loop end-to-end before any agent exists

## 9. Phase 2 Exit Checklist
- [ ] Both frozen worlds assembled; leakage audits passed and recorded in manifests
- [ ] Ground truth built for 2024 and 2025 seasons under exact league scoring
- [ ] Baseline bank scored on both worlds; decision-log entry written with the numbers
- [ ] One-command runner works; scorecards archive with config hashes; trend index renders
- [ ] Bootstrap CI implementation verified against a hand-checked example
- [ ] Dry run completed (baseline-as-candidate scored identically to its baseline-bank entry)
