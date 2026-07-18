# Phase 5 — Ops Layer Design (Weekly Thursday Edition)
## Silent Daily Capture + Thursday Morning Analysis Brief

*(Numbering note: the recurring monitoring task lives in Phase 5 of the WBS — Phase 6 is the draft deliverables. This design implements your requested change: analysis and briefing move from daily to weekly, Thursday mornings.)*

---

## Design Notes (how the research shaped this)

- **Cadence should match the decision, not the data.** Reporting-cadence best practice maps daily rhythms to operational firefighting and weekly rhythms to team-level execution review — and pre-draft ranking maintenance is execution review, not firefighting. A weekly Thursday brief fits the actual decision tempo: you don't re-rank a July draft board because Tuesday's ADP wiggled.
- **Cadence must also match capacity:** if you can't triage a stream of alerts, scheduling them only creates fatigue. One person with a job reading one good brief a week beats skimming seven mediocre ones — and when repeated alerts are rarely followed by consequences, attention to them decays, which is exactly how a daily brief dies by week three.
- **The actionability rule governs everything here:** if an alert fires and the recipient cannot take a specific action, the alert should not exist. Applied: routine movement goes silently into the changelog (a report artifact); only events you'd act on before next Thursday interrupt you.
- **Separate noise from criticals structurally, not by willpower.** Effective monitoring routes low-priority information to dashboards and reports while only high-value, actionable alerts notify a human. This design has exactly two channels: the Thursday brief (report) and a narrow interrupt alert (notification), with an explicit list of what qualifies for the latter.
- **The one thing that CANNOT go weekly: data capture.** Daily ADP/injury/depth-chart snapshots are un-backfillable (Phase 1). Capture stays daily and silent — zero human attention. Weekly applies to *analysis and briefing* only. Skipping daily capture to "go weekly" would quietly destroy Phase 2's future frozen worlds.
- **Audit the alert rules themselves on a schedule** — reviews should ask whether alerts led to action and whether thresholds still make sense; a monthly check is built into the brief.

---

## 1. Two-Tier Architecture

```
TIER 1 — CAPTURE (daily, silent, no human attention)
  Every day ~06:00 ET: pull ADP, injury designations, trending,
  depth charts, news → raw snapshot + validation + GOLD marking.
  Output: data only. No brief, no notification (except job-failure).

TIER 2 — ANALYZE & BRIEF (weekly, Thursday 07:00 ET)
  Diff the full 7-day window, apply trigger rules, run any
  triggered re-evaluations, write changelog entries,
  deliver the Thursday Brief.

INTERRUPT CHANNEL (event-driven, rare by design)
  A short list of act-before-Thursday events (see §4) notifies
  immediately. Everything else waits for Thursday.
```

Why Thursday specifically works well: weekend + early-week news (camp reports, preseason games in August, weekend mock-draft ADP shifts) has settled by Thursday, and the brief lands ahead of weekend draft prep and league chatter. Same reason PMs put pipeline reviews before the week they inform.

## 2. Tier 2 — The Thursday Job (runs before the brief)

1. **Window diff:** compare today's gold snapshot vs. last Thursday's across ADP (7-day delta, plus intra-week spike detection so a Monday spike-and-revert isn't invisible), injury designations, depth charts, transactions, and trending adds/drops.
2. **Trigger evaluation:** apply the rules in §3 to every diff line.
3. **Re-evaluation runs:** for each fired trigger, re-run the mapped agent(s) + judge on the affected player only (per WBS 5.3 — never a full-board rebuild).
4. **Changelog append:** one structured record per event: `date | player | event | source | assessed_impact (none/watch/re-rank) | action_taken`.
5. **Brief assembly:** render §5 format; deliver.

## 3. Trigger Rules (weekly-calibrated)

| Event class | Threshold (weekly window) | Action |
|---|---|---|
| ADP movement | > 6 spots (rounds 1-5 players) or > 12 spots (later) over 7 days | Market agent re-eval; classify info/hype/bias |
| Depth chart change | Starter/committee change at a rostered-relevance position | Opportunity + context agents re-eval |
| Injury designation | New multi-week designation on universe player | Affected agents re-eval; adjacent beneficiaries flagged |
| Transaction | Trade/signing/release touching a universe player's team | Context agent re-eval team; opportunity agent re-eval shares |
| Thesis check | Any event matching a My Guy's pre-registered invalidation trigger or a judge's dissent_watch | Full mini-debate on that player (the ONE case that earns it) |
| Trending anomaly | Sleeper trending spike without any matching event above | Watch-list only — never a re-rank by itself |

Thresholds are wider than the daily design's would be (a 7-day window accumulates more routine drift); they live in config, version-tagged, and get audited monthly (§6).

## 4. Interrupt Channel (the ONLY things that don't wait for Thursday)

Strictly limited to events where waiting could cost you an action window:

1. A My Guy's pre-registered invalidation trigger fires (season-ending injury, trade to a buried role)
2. Season-altering injury to any current top-30 overall board player
3. Draft-week window (see §5 ramp) — any trigger-class event
4. Capture-job failure: 2 consecutive missed daily snapshots (this is R2 — the un-backfillable data is at risk)

Everything else — every ADP wiggle, every camp hype note, every beat-writer tea leaf — waits for Thursday. If the interrupt channel fires more than ~once a fortnight outside draft week, its rules are too loose; tighten at the monthly audit.

## 5. The Thursday Brief (format contract)

Hard cap: one screen. Sections in order:

```
THURSDAY BRIEF — {date} | Snapshot: GOLD {date} | Days to draft: N
1. ACTIONS TAKEN (0-5 lines): ranks changed this week and why
   — or "No changes; board stands."
2. TRIGGERS FIRED, NO ACTION (0-4 lines): evaluated, held, reason
3. WATCH LIST (≤5 lines): building-not-fired items incl. My Guys
   thesis-health one-liners and dissent_watch status
4. MARKET NOTE (2-3 lines): the week's biggest value gaps between
   the board and current ADP (draft-cost intelligence)
5. SYSTEM HEALTH (1 line): capture streak, validation status,
   token spend vs. budget
```

Rendering rule: if nothing happened, the brief says so in three lines and stops. Padding a quiet week teaches you to skim, and skimming is how the one important Thursday gets missed.

**Draft-week ramp (recommended, pre-agreed in config):** from 10 days before the draft, Tier 2 runs daily and §4's interrupt list widens to all trigger classes. News velocity in late August genuinely changes — camp battles resolve, preseason injuries hit — and cadence should be designed deliberately against what it costs to miss, which spikes in that window. The ramp is scheduled in advance so it's a design decision, not a panic response. After the draft: Tier 2 returns to Thursdays (dynasty changelog value) or pauses — your call at the time.

## 6. Reliability & Audits

- Capture-job monitoring: daily success ping to a status file; §4.4 interrupt on 2 misses; weekly streak reported in the brief
- Retry logic: 3 attempts with backoff per source; partial snapshots marked non-GOLD and excluded from diffs
- Monthly rule audit (first Thursday): for each trigger class — did fires lead to action? Any missed event the brief should have caught? Thresholds adjusted via decision-log entry (alert configs should be reviewed on a schedule, focusing on whether alerts led to action and whether thresholds still make sense)
- Every brief archived; Phase 7 reads the changelog + brief archive to score whether in-season signals were caught, missed, or over-reacted to

## 7. Phase 5 Exit Checklist
- [ ] Tier 1 capture verified daily for 7+ consecutive days (silent)
- [ ] First Thursday run executes end-to-end: diff → triggers → changelog → brief
- [ ] Interrupt channel tested with a synthetic My-Guy invalidation event
- [ ] Draft-week ramp date computed from Charter draft date and scheduled
- [ ] Trigger thresholds committed to config with version tag; monthly audit reminder set
- [ ] One real weekly cycle reviewed: brief fit on one screen and was actually read
