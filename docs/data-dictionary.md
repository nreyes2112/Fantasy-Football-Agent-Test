# Data Dictionary

*Implements: phase1-data-platform-design.md §5 — "One YAML/markdown file, versioned in Git, defining every derived metric ONCE. Agents and harness both read definitions from here — no metric is ever defined inside a prompt or notebook."*

Every metric below states whether it's actually computable **today**, from what's actually been pulled (`capture/pull_stats.py`: nflverse `player_stats`/`team_stats`, 2024-2025 seasons — see [CLAUDE.md](../CLAUDE.md)). Metrics marked **NOT YET COMPUTABLE** are honestly documented as gaps, not fabricated or silently omitted — per the project's no-fabrication rule, an agent asking for one should get a clear "unavailable" answer, never a guess.

Computable metrics are implemented in [`access/metrics.py`](../access/metrics.py) and exposed through [`access/layer.py`](../access/layer.py)'s `get_player_stats()` (per-player metrics) or as standalone functions.

---

```
metric: target_share
definition: player targets / team pass attempts, per game window
formula: sum(targets) / sum(team pass attempts)  -- literal nflverse column, no derivation needed
windows: [season, last8, last4]
source_tables: [raw/nflverse/player_stats]
stability: HIGH
notes: computed and provided directly by nflverse's load_player_stats(); pass through get_player_stats(metrics=["target_share"]). "post_event" window (a role-change-triggered lookback) is NOT supported yet -- needs the role-change detection Phase 5's ops layer will eventually do.
status: COMPUTABLE (implemented, verified)
```

```
metric: carry_share
definition: player carries / team carries, per game window
formula: sum(player carries) / sum(team carries), matched by (team, season, week) so a mid-season team change doesn't pollute the ratio
windows: [season, last8, last4]
source_tables: [raw/nflverse/player_stats, raw/nflverse/team_stats]
stability: HIGH
notes: implemented as access.metrics.compute_carry_share(); exposed via get_player_stats(metrics=["carry_share"])
status: COMPUTABLE (implemented, verified against Jahmyr Gibbs' 2025 season: 54.98%, consistent with a near-even committee split with Detroit's RB2)
```

```
metric: snap_share
definition: player offensive snaps / team offensive snaps, per game window
formula: mean(offense_pct) across the window's games -- offense_pct is already computed directly by Pro Football Reference/nflverse (load_snap_counts), no re-derivation needed; averaged across games the same way target_share/air_yards_share are (a rate stat, not summed)
windows: [season, last8, last4]
source_tables: [curated/snap_counts_resolved]
stability: HIGH
notes: NOT fabricated from proxies (e.g. touches) -- snap share and touch share diverge meaningfully for pass-blocking backs, decoy routes, etc. This table is pfr_player_id-keyed, not natively gsis_id like player_stats/team_stats -- resolved via nflverse_crosswalk's pfr_id column, reusing the same resolve_source() machinery Sleeper/ESPN use (capture/pull_crosswalk.py), matched to the SAME (season, week) games already selected for the requested window so "last8" means the same 8 games regardless of which metric is asked for.
status: COMPUTABLE (implemented, verified: Josh Allen 2025 season = 98.11%, correct for a full-time starting QB; Jahmyr Gibbs 2025 season = 67%, sensibly higher than his 54.98% carry_share since he's also used on passing downs)
```

```
metric: route_participation
definition: routes run by player / team's total pass plays, per game window
formula: sum(player routes) / sum(team dropbacks)
windows: [season, last8, last4]
source_tables: none yet -- needs nflreadpy's load_participation() or load_nextgen_stats(), not pulled
stability: HIGH (when available)
notes: distinct from target_share -- a receiver can run every route and still not be targeted; this is a volume-opportunity metric, target_share is a conversion-of-opportunity metric
status: NOT YET COMPUTABLE
```

```
metric: weighted_opportunity
definition: a single number blending target volume and quality (air yards) into one opportunity score; the design's own spec calls for red-zone/end-zone touches to be weighted more heavily
formula (interim stand-in): nflverse's own `wopr` column = 1.5 x target_share + 0.7 x air_yards_share (the published WOPR formula, Josh Hermsmeyer) -- NOT custom red-zone-weighted per the original spec, even though red-zone-specific target/carry counts ARE now pulled (D-017, see red_zone_target_share/red_zone_carry_share below) -- combining them into ONE blended score would mean inventing a weighting formula from scratch, which is a design decision this project hasn't made, not a data gap
windows: [season, last8, last4]
source_tables: [raw/nflverse/player_stats]
stability: MEDIUM -- real metric, but known to differ from the design's original red-zone-weighted intent
notes: get_player_stats(metrics=["wopr"]) works today as an interim stand-in. D-017 makes a true red-zone-weighted version buildable (the raw ingredients exist), but the blending formula itself is deferred pending an actual design decision -- an agent can use red_zone_target_share/red_zone_carry_share directly alongside wopr instead of waiting for a single combined number.
status: PARTIALLY COMPUTABLE (nflverse's wopr proxy only; red-zone-specific components now separately computable, see below)
```

```
metric: red_zone_target_share
definition: player's share of the TEAM's red-zone (yardline_100 <= 20) pass attempts -- targets a receiver draws inside the scoring area specifically, distinct from season-long target_share
formula: sum(player red-zone targets) / sum(team red-zone pass attempts), matched game-by-game (same team/season/week)
windows: [season, last8, last4]
source_tables: [curated/weekly_stats (rz_targets column, joined from curated/redzone_player_stats), curated/redzone_team_stats]
stability: MEDIUM -- red-zone volume is a smaller, noisier sample than season-long targets, but directly reflects a team's scoring-situation usage plan
notes: built for agents/prompts.py's opportunity_analyst methodology, which named this exact gap in its own D-015/D-016 output ("weighted opportunity ... red-zone and end-zone usage weighted up"). Implemented as access.metrics.compute_red_zone_target_share(); exposed via get_player_stats(metrics=["red_zone_target_share"]). Sourced from capture/pull_pbp.py (D-017) -- an aggregated pbp summary, NOT the full play-by-play table, which is never persisted (repo-size deviation, see that module's docstring).
status: COMPUTABLE (implemented 2026-07-19, hand-verified: Saquon Barkley's 2024 red-zone carries/TDs matched pre-confirmed real facts exactly -- 64 carries, 6 TDs -- at the raw aggregation level; the share computation itself reconciled exactly [0.4604] once the established per-game-matched methodology, same as carry_share, was applied to the correct 16-game denominator)
```

```
metric: red_zone_carry_share
definition: player's share of the TEAM's red-zone (yardline_100 <= 20) rush attempts
formula: sum(player red-zone carries) / sum(team red-zone rush attempts), matched game-by-game
windows: [season, last8, last4]
source_tables: [curated/weekly_stats (rz_carries column), curated/redzone_team_stats]
stability: MEDIUM -- same red-zone-sample-size caveat as red_zone_target_share; often the clearest signal for goal-line-role questions (e.g. a change-of-pace back with a real season carry_share but near-zero red_zone_carry_share)
notes: implemented as access.metrics.compute_red_zone_carry_share(); exposed via get_player_stats(metrics=["red_zone_carry_share"])
status: COMPUTABLE (implemented 2026-07-19, same verification as red_zone_target_share)
```

```
metric: designed_run_rate
definition: for QBs, the share of a player's rush attempts that were called runs vs. broken-pocket scrambles -- separates a designed rushing role (stable, scheme-driven) from scramble production (less repeatable, more QB-play-quality-dependent)
formula: designed_carries / (designed_carries + scramble_carries), where designed_carries excludes plays flagged qb_scramble AND qb_kneel (kneel-downs are clock management, not a rushing opportunity signal either way)
windows: [season, last8, last4]
source_tables: [curated/weekly_stats (designed_carries/scramble_carries columns)]
stability: HIGH for QBs with real rushing volume; meaningless (returns ~1.0, not an error) for non-QBs, since scramble_carries is structurally 0 for anyone who was never the passer on the play -- agents must gate on position before reading this metric, same as any other position-scoped field
notes: built for agents/prompts.py's opportunity_analyst methodology, which named this exact gap in its D-015/D-016 QB runs ("designed-run vs scramble split... not retrievable"). Implemented as access.metrics.compute_designed_run_rate(); exposed via get_player_stats(metrics=["designed_run_rate"])
status: COMPUTABLE (implemented 2026-07-19, hand-verified: Lamar Jackson's 2024 designed/scramble split matched a pre-confirmed real fact exactly -- 83 designed carries, 45 scrambles, 12 kneels excluded -- both at the raw aggregation level and through the full get_player_stats() call path, live and frozen-world modes both tested)
```

```
metric: YPRR
definition: yards per route run
formula: sum(receiving_yards) / sum(routes run)
windows: [season, last8, last4]
source_tables: none yet -- needs routes-run data (load_participation / load_nextgen_stats), not pulled
stability: HIGH (when available)
notes: widely considered one of the most stable/predictive receiving efficiency metrics in fantasy analytics -- worth prioritizing once routes data is pulled
status: NOT YET COMPUTABLE
```

```
metric: aDOT
definition: average depth of target -- how far downfield a player is targeted, on average
formula: sum(receiving_air_yards) / sum(targets)
windows: [season, last8, last4]
source_tables: [raw/nflverse/player_stats]
stability: HIGH
notes: implemented as access.metrics.compute_adot(); exposed via get_player_stats(metrics=["aDOT"])
status: COMPUTABLE (implemented, verified: Jahmyr Gibbs' 2025 aDOT = 0.57, correctly very low for a receiving back who mostly catches passes at or behind the line of scrimmage)
```

```
metric: air_yards_share
definition: player's share of the team's total receiving air yards
formula: sum(player receiving_air_yards) / sum(team receiving_air_yards) -- literal nflverse column
windows: [season, last8, last4]
source_tables: [raw/nflverse/player_stats]
stability: HIGH
notes: computed and provided directly by nflverse; pass through get_player_stats(metrics=["air_yards_share"])
status: COMPUTABLE (implemented, verified)
```

```
metric: EPA_per_target
definition: expected points added per target -- an efficiency metric independent of raw volume
formula: sum(receiving_epa) / sum(targets)
windows: [season, last8, last4]
source_tables: [raw/nflverse/player_stats]
stability: MEDIUM -- EPA itself is fairly stable year-over-year for established players, less so on small samples (single games)
notes: implemented as access.metrics.compute_epa_per_target(); exposed via get_player_stats(metrics=["EPA_per_target"])
status: COMPUTABLE (implemented, verified: Jahmyr Gibbs' 2025 season = 0.1888, a solidly positive per-target value)
```

```
metric: success_rate
definition: % of plays with positive EPA (or meeting a down/distance-specific success threshold)
formula: count(plays where success==1) / count(plays)
windows: [season, last8, last4]
source_tables: none yet -- needs play-level down/distance context (load_pbp), not pulled; player_stats is a game-level aggregate and doesn't carry a per-play success flag
stability: HIGH (when available)
notes: cannot be approximated from EPA_per_target or similar aggregates without fabricating a threshold -- explicitly deferred rather than guessed
status: NOT YET COMPUTABLE
```

```
metric: TD_rate
definition: touchdowns per touch (carries + targets)
formula: (sum(rushing_tds) + sum(receiving_tds)) / (sum(carries) + sum(targets))
windows: [season, last8, last4]
source_tables: [raw/nflverse/player_stats]
stability: LOW -- regression-mandatory flag (phase1 §5 explicitly calls this out: TD rate on typical fantasy sample sizes is mostly variance, not repeatable skill; the efficiency agent's regression-to-mean mandate and the judge's "stable metrics outweigh unstable ones" rubric line both key off this field)
notes: implemented as access.metrics.compute_td_rate(); exposed via get_player_stats(metrics=["TD_rate"])
status: COMPUTABLE (implemented, verified)
```

```
metric: pass_rate_over_expectation (PROE)
definition: team pass rate minus the pass rate a play-calling model would predict given score/time/down/distance
formula: team pass_rate - expected_pass_rate(game_state)
windows: [season, last8, last4]
source_tables: none -- needs a play-calling expectation model conditioned on game state, which requires play-level data (load_pbp) AND a fitted model, neither of which exist
stability: HIGH (when available -- PROE is considered one of the more stable team-context signals)
notes: raw pass_rate (not "over expectation") IS available via get_team_context() -- do not substitute one for the other, they answer different questions (how much a team passes vs. how much MORE/LESS than expected)
status: NOT YET COMPUTABLE
```

```
metric: team_plays_per_game
definition: team offensive plays per game (pass attempts + rush attempts)
formula: mean(team attempts + team carries, per game)
windows: [season, last8, last4]
source_tables: [raw/nflverse/team_stats]
stability: HIGH
notes: implemented in get_team_context() (field: plays_per_game)
status: COMPUTABLE (implemented, verified: Detroit 60.24 plays/game and Baltimore 54.65 plays/game in 2025, consistent with their real offensive tempos)
```

```
metric: vacated_targets / vacated_carries
definition: targets/carries accounted for by players who had volume on a team last season but are no longer on that team
formula: sum(targets or carries) for players where prior-season team == X AND current team (per Sleeper's live team field) != X
windows: [season-over-season only -- not a rolling window]
source_tables: [raw/nflverse/player_stats, curated/sleeper_resolved]
stability: HIGH
notes: implemented in get_vacated_opportunity(); reuses Sleeper's live current-team field rather than a separate nflverse rosters pull, since "who's on the team right now" is exactly what that field already tracks. "Departed" includes trades, free-agent signings elsewhere, retirements, and unsigned free agents alike.
status: COMPUTABLE (implemented, verified: Trent Sherfield/Elijah Moore correctly identified as having left Denver; Bo Nix/Courtland Sutton correctly NOT flagged)
```

```
metric: age_curve_position
definition: where a player sits on a typical age-performance curve for their position (e.g. "still ascending," "at peak," "past the cliff")
formula: NONE DEFINED YET -- this requires choosing/fitting a position-specific age curve (a methodology decision), not just pulling more data
windows: n/a
source_tables: [curated/nflverse_crosswalk (has age/birthdate)] -- the raw input exists, the curve model does not
stability: n/a until a model is chosen
notes: deliberately deferred to Phase 3 (agent methodology) rather than guessed here -- an arbitrary curve baked into the data dictionary would be exactly the kind of unexamined assumption the project's pre-registration principle exists to prevent
status: NOT YET COMPUTABLE (methodology decision, not a data gap)
```

```
metric: draft_capital_tier
definition: a coarse bucketing of draft capital into Premium/Mid/Late/Undrafted
formula: Premium = rounds 1-2, Mid = rounds 3-4, Late = rounds 5-7, Undrafted = no draft_round
windows: n/a (static per player)
source_tables: [curated/nflverse_crosswalk]
stability: HIGH (draft capital doesn't change)
notes: implemented as access.metrics.draft_capital_tier(draft_round). Round boundaries are a build-time judgment call, not a published standard -- worth a decision-log entry if these boundaries get revisited based on backtest results (phase1 §6's "a rule change is a decision-log entry" principle applies here too).
status: COMPUTABLE (implemented, verified: Jahmyr Gibbs/Jared Goff -> Premium (round 1); Puka Nacua -> Late (round 5), correctly distinguishing draft capital from his elite actual production)
```

```
metric: ADP (raw)
definition: this league's current average draft position
formula: n/a -- pulled directly from ESPN (primary, D-005) and FantasyFootballCalculator (secondary, D-007)
windows: [current snapshot only]
source_tables: [curated/espn_resolved, curated/ffc_proposed_matches]
stability: HIGH
notes: implemented in get_adp()
status: COMPUTABLE (implemented, verified)
```

```
metric: ADP deltas (7/14/30-day)
definition: how much a player's ADP has moved over the trailing N days
formula: adp(today) - adp(today - N days)
windows: [7, 14, 30 days]
source_tables: [curated/espn_resolved, curated/ffc_proposed_matches, across multiple dated snapshots]
stability: HIGH once enough history exists
notes: get_adp()'s `history` field already supports multi-day lookback (via `history_days`) and honestly reports how many days of snapshot history actually exist vs. requested -- but as of this writing, this project has accumulated well under 7 days of real snapshot history, so no delta longer than what's actually been captured can be trusted yet. Not a code gap -- a time-accumulation gap that closes automatically as the daily/weekly cron keeps running.
status: PARTIALLY COMPUTABLE (mechanism exists; insufficient historical depth so far)
```

```
metric: PPG (fantasy points per game, under this league's exact scoring)
definition: average fantasy points per game, scored under THIS league's real settings (charter §5: 4pt passing TD, 1.0 PPR, no yardage/performance bonuses) -- NOT nflverse's own fantasy_points_ppr column, which uses nflverse's own scoring assumptions
formula: sum(counting_stat x this_league's_points_per_unit, for every scored stat category) / games_in_window
windows: [season, last8, last4]
source_tables: [raw/nflverse/player_stats, ESPN live scoring settings via get_league_scoring()]
stability: HIGH (it's an accounting identity, not an estimate)
notes: implemented as access.metrics.compute_fantasy_points() + league_scoring_by_column(), exposed via get_player_stats(metrics=["fantasy_points_league_ppg"]). Scoring-rule-to-column mapping (access/metrics.py's ESPN_STAT_ID_TO_COLUMN) verified against the community-maintained espn-api project's PLAYER_STATS_MAP and cross-checked against this league's own live scoring items. HAND-VERIFIED against two real 2025 week-1 stat lines per phase1 §5's explicit requirement: Josh Allen (394 pass yds, 2 pass TD, 30 rush yds, 2 rush TD -> hand calc 38.76, code result 38.76, exact match) and Jahmyr Gibbs (19 rush yds, 10 receptions, 31 rec yds -> hand calc 15.0, code result 15.0, exact match, specifically exercising the PPR/reception scoring path Allen's game didn't touch).
status: COMPUTABLE (implemented, hand-verified against 2 independent real examples)
```

---

## Summary

| Status | Count | Metrics |
|---|---|---|
| Computable (implemented + verified) | 11 | target_share, carry_share, **snap_share**, aDOT, air_yards_share, EPA_per_target, TD_rate, team_plays_per_game, vacated_targets/carries, draft_capital_tier, ADP (raw), **PPG (hand-verified)** |
| Partially computable | 2 | weighted_opportunity (nflverse's `wopr` as an interim stand-in, not the red-zone-weighted version originally specified), ADP deltas (mechanism built, needs more days of snapshot history to accumulate) |
| Not yet computable | 5 | route_participation, YPRR, success_rate, pass_rate_over_expectation, age_curve_position (a methodology decision, not a data gap) |

This table counts phase1 §5's original 18-metric spec only. **D-017 (2026-07-19) adds 3 extension metrics beyond that original list**, built specifically to close a gap Agent 1's own opportunity/volume methodology named in its D-015/D-016 output (not part of the original spec, so counted separately rather than inflating the 18): **red_zone_target_share**, **red_zone_carry_share**, **designed_run_rate** -- all COMPUTABLE, hand-verified against pre-confirmed real facts (Saquon Barkley's 2024 red-zone carries/TDs, Lamar Jackson's 2024 designed/scramble split), sourced from `capture/pull_pbp.py`'s aggregated red-zone/rush-type summary (full play-by-play is never persisted -- see that module's docstring for the repo-size deviation this required). This also means `load_pbp` -- previously listed as "not currently blocking anything" in Phase 1's exit notes -- is now partially pulled, though only as this narrow aggregation; `success_rate`/`pass_rate_over_expectation` still need the full play-level table (or, per nflverse's pbp already carrying a `success` column verified 2026-07-19, `success_rate` specifically may now be a much smaller lift than previously scoped -- flagged for whoever builds Agent 2's efficiency methodology, not pursued now to keep D-017's scope to what Agent 1 actually needed).

`load_snap_counts` is done (2026-07-18) -- snap_share moved from "not yet computable" to computable. Closing the rest of the list needs `load_participation`/`load_nextgen_stats` (route_participation/YPRR) and one bigger lift, `load_pbp` (success_rate/PROE) — see [PROJECT-BRIEF.md](PROJECT-BRIEF.md) §7 for current priority.
