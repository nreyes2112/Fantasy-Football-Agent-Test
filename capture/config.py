"""League constants used by capture jobs. Values come from charter.md §5 (frozen)."""

from zoneinfo import ZoneInfo

LEAGUE_TIMEZONE = ZoneInfo("America/New_York")
LEAGUE_TEAMS = 12
LEAGUE_SCORING = "ppr"  # charter.md §5: 1.0 PPR

SNAPSHOT_ROOT = "data/snapshots"
SCHEMA_ROOT = "data/schemas"
