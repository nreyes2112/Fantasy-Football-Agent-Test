"""Tier 1 daily capture job (phase1-data-platform-design.md §2, §8).

Pulls, in order: Sleeper player meta/injury status, Sleeper trending
adds/drops, ADP from FantasyFootballCalculator (secondary/backup market
source per D-007), and this league's own ESPN ADP/ownership view (PRIMARY
market source per D-005). Writes one dated, immutable raw snapshot per §4.

ESPN requires the owner's SWID/espn_s2 cookies (env vars ESPN_SWID/ESPN_S2,
local .env or a CI secret -- never committed). If they aren't configured yet,
the ESPN pull is skipped (not failed) so Sleeper/ADP capture -- the
un-backfillable data -- is never blocked by ESPN auth. If they ARE configured
but the pull fails (expired cookies, ESPN outage), that's recorded as a
failing check so the run goes red without losing the rest of the snapshot.

Usage:
    python -m capture.pull_daily

Exit code is non-zero on any pull failure or failed sanity check, so a
GitHub Actions run shows red and pages nobody but fails loudly (phase5's
"no notification except job-failure" rule).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from dotenv import load_dotenv

from capture.config import ESPN_LEAGUE_ID, ESPN_SEASON, LEAGUE_SCORING, LEAGUE_TEAMS, LEAGUE_TIMEZONE
from capture.snapshot import SnapshotWriter, today_snapshot_date
from capture.sources import espn, ffc_adp, sleeper

MIN_SLEEPER_PLAYERS = 8000  # observed ~12,200; generous floor against a truncated pull
MIN_ADP_PLAYERS = 50
MIN_ESPN_PLAYERS = 400  # pulled with limit=600; floor guards a truncated/filtered response


def run() -> int:
    load_dotenv()
    date = today_snapshot_date()
    writer = SnapshotWriter(date)

    if writer.already_captured():
        print(f"[capture] snapshot for {date} already exists, nothing to do")
        return 0

    endpoints_used = {}

    # --- Sleeper: player meta + injury status ---
    print("[capture] pulling Sleeper player list...")
    players = sleeper.fetch_players()
    players_df = sleeper.players_to_dataframe(players)
    writer.write_table("sleeper", "players", players_df)
    writer.record_check(
        "sleeper_players_min_rows",
        len(players_df) >= MIN_SLEEPER_PLAYERS,
        f"{len(players_df)} rows (floor {MIN_SLEEPER_PLAYERS})",
    )
    writer.write_schema(
        "sleeper_players",
        {c: "see Sleeper /players/nfl docs; nested fields JSON-encoded to string" for c in players_df.columns},
    )
    endpoints_used["sleeper_players"] = sleeper.PLAYERS_URL

    # --- Sleeper: trending adds/drops ---
    for direction in ("add", "drop"):
        print(f"[capture] pulling Sleeper trending {direction}s...")
        trending = sleeper.fetch_trending(direction)
        trending_df = sleeper.trending_to_dataframe(trending)
        table_name = f"trending_{direction}s"
        writer.write_table("sleeper", table_name, trending_df)
        writer.record_check(
            f"sleeper_{table_name}_non_empty",
            len(trending_df) > 0,
            f"{len(trending_df)} rows",
        )
        writer.write_schema(
            f"sleeper_{table_name}",
            {"player_id": "sleeper player_id", "count": f"number of {direction}s in lookback window"},
        )
        endpoints_used[f"sleeper_{table_name}"] = sleeper.TRENDING_URL_TEMPLATE.format(direction=direction)

    # --- ADP (FantasyFootballCalculator; see D-007) ---
    year = datetime.now(LEAGUE_TIMEZONE).year
    print(f"[capture] pulling FFC ADP ({LEAGUE_SCORING}, {LEAGUE_TEAMS}-team, {year})...")
    adp_payload = ffc_adp.fetch_adp(scoring=LEAGUE_SCORING, teams=LEAGUE_TEAMS, year=year)
    adp_df = ffc_adp.adp_to_dataframe(adp_payload)
    writer.write_table("ffc", "adp", adp_df)
    writer.record_check(
        "ffc_adp_min_rows",
        len(adp_df) >= MIN_ADP_PLAYERS,
        f"{len(adp_df)} rows (floor {MIN_ADP_PLAYERS})",
    )
    writer.write_schema(
        "ffc_adp",
        {
            "player_id": "FFC internal player id (NOT gsis_id/sleeper_id -- crosswalk not yet built)",
            "name": "player full name (join risk -- name-based, per §3 human-confirmed only)",
            "position": "position",
            "team": "NFL team abbreviation",
            "adp": "average draft position (float, overall pick number)",
            "adp_formatted": "round.pick string",
            "times_drafted": "sample size behind this ADP value",
            "high": "earliest pick observed",
            "low": "latest pick observed",
            "stdev": "standard deviation of pick position",
            "bye": "bye week",
        },
    )
    endpoints_used["ffc_adp"] = f"{ffc_adp.BASE_URL}/{LEAGUE_SCORING}"
    adp_meta = ffc_adp.adp_meta(adp_payload)
    print(f"[capture] FFC ADP sample: {adp_meta}")

    # --- ESPN: this league's own ADP/ownership (PRIMARY market source, D-005) ---
    swid, espn_s2 = os.environ.get("ESPN_SWID"), os.environ.get("ESPN_S2")
    if not swid or not espn_s2:
        print("[capture] ESPN_SWID/ESPN_S2 not configured -- skipping ESPN pull (not a failure)")
    else:
        try:
            print(f"[capture] pulling ESPN player pool (league {ESPN_LEAGUE_ID}, season {ESPN_SEASON})...")
            espn_players = espn.fetch_player_pool(ESPN_SEASON, ESPN_LEAGUE_ID, swid, espn_s2)
            espn_df = espn.player_pool_to_dataframe(espn_players)
            writer.write_table("espn", "player_pool", espn_df)
            writer.record_check(
                "espn_player_pool_min_rows",
                len(espn_df) >= MIN_ESPN_PLAYERS,
                f"{len(espn_df)} rows (floor {MIN_ESPN_PLAYERS})",
            )
            writer.write_schema(
                "espn_player_pool",
                {
                    "player_id": "ESPN internal player id (NOT gsis_id/sleeper_id -- crosswalk not yet built)",
                    "full_name": "player full name (join risk -- name-based, per §3 human-confirmed only)",
                    "position": "position (mapped from ESPN defaultPositionId)",
                    "pro_team_id": "ESPN internal NFL team id",
                    "injury_status": "ESPN injury designation",
                    "average_draft_position": "this league's ESPN ADP -- PRIMARY market source per D-005",
                    "average_draft_position_pct_change": "day-over-day ADP percent change",
                    "percent_owned": "percent of ESPN leagues rostering this player",
                    "percent_started": "percent of ESPN leagues starting this player",
                    "auction_value_average": "ESPN auction value average",
                },
            )
            endpoints_used["espn_player_pool"] = f"{espn.BASE_URL}/{ESPN_SEASON}/segments/0/leagues/{ESPN_LEAGUE_ID}?view=kona_player_info"
        except espn.EspnAuthError as e:
            print(f"[capture] ESPN AUTH FAILURE (cookies likely expired): {e}")
            writer.record_check("espn_player_pool_pulled", False, str(e))
        except Exception as e:
            print(f"[capture] ESPN pull failed: {e}")
            writer.record_check("espn_player_pool_pulled", False, str(e))

    all_passed = writer.all_checks_passed()
    manifest_path = writer.finalize(source_endpoints=endpoints_used)

    print(f"[capture] wrote {manifest_path} (all_checks_passed={all_passed})")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(run())
