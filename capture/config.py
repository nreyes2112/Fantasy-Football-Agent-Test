"""League constants used by capture jobs. Values come from charter.md §5 (frozen)."""

from zoneinfo import ZoneInfo

LEAGUE_TIMEZONE = ZoneInfo("America/New_York")
LEAGUE_TEAMS = 12
LEAGUE_SCORING = "ppr"  # charter.md §5: 1.0 PPR

ESPN_LEAGUE_ID = 94172663
ESPN_SEASON = 2026  # season the Aug 2026 draft belongs to

SNAPSHOT_ROOT = "data/snapshots"
SCHEMA_ROOT = "data/schemas"

# Charter §5 (frozen) -- the settings-diff check compares ESPN's live settings
# against these. A mismatch means either the charter is stale or the league
# commissioner changed something after charter approval; either way it's a
# decision-log-worthy event, not a silent auto-correct.
CHARTER_ROSTER_SLOTS = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "RB/WR/TE": 1,  # FLEX
    "K": 1,
    "D/ST": 1,
    "BE": 7,
    "IR": 1,  # charter.md §5: "7 bench ... + IR if enabled" -- confirmed enabled 2026-07-18
}
CHARTER_PASSING_TD_POINTS = 4.0
CHARTER_RECEPTION_POINTS = 1.0  # 1.0 PPR
