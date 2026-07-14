"""
pipeline/sports.py
===================
Sport-selection config layer — the one seam that turns the MLB-hardcoded
pipeline into a sport-agnostic core with pluggable per-sport modules.

Everything that used to be an implicit "this is baseball" assumption lives
here as data: the odds-API sport/market keys (were class constants on
`betting.odds_loader.TheOddsAPISource`), the model target column, the grain
column names, the wide Gold table names, the per-sport Bronze `source_root`
map, and the league-average baselines (moved out of `pipeline.config`).

The tunable Voss Edge L1–L5 rubric stays in `config/voss_edge.yaml` — that
is deliberately *configuration a human retunes from backtests*, not
structural code, so it does not belong here.

Add a sport by adding a `SportConfig` to `REGISTRY`; no core code changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SportConfig:
    """Everything sport-specific the core needs, as data."""

    sport_key: str                       # 'mlb' | 'nfl' | 'cfb'  (== event/entity PK partition)
    display_name: str
    entity_noun: str                     # 'batter', 'passer'   (for generic UI / narration)
    target: str                          # model label column, e.g. 'hr_hit'

    # Grain of the wide matchup table (generic core maps these → entity_id/event_id)
    entity_grain: str                    # 'batter_id'
    event_grain: str                     # 'game_pk'

    # Gold table names: the DuckDB build table vs the Postgres serving table
    build_table: str                     # DuckDB table produced by the Gold layer
    wide_table: str                      # per-sport serving table in Supabase

    # Odds API adapter parameters (were TheOddsAPISource.SPORT / .MARKET)
    odds_api_sport: str
    odds_api_market: str
    side_yes_label: str = "Over"         # outcome name the book uses for the "Yes" side

    # Bronze source_root map per feed-role. Values are the canonical source_root
    # names — dedupe rule: distinct roots only (see docs; e.g. FanGraphs xStats
    # collapse into 'statcast'; nflverse-injuries collapse into 'espn').
    bronze_sources: dict = field(default_factory=dict)

    # Per-sport league-average baselines (moved out of pipeline.config.LEAGUE_AVG).
    league_avg: dict = field(default_factory=dict)


REGISTRY: dict[str, SportConfig] = {
    "mlb": SportConfig(
        sport_key="mlb",
        display_name="MLB",
        entity_noun="batter",
        target="hr_hit",
        entity_grain="batter_id",
        event_grain="game_pk",
        build_table="batter_matchup_features",
        wide_table="mlb_matchup_features",
        odds_api_sport="baseball_mlb",
        odds_api_market="batter_home_runs",
        side_yes_label="Over",
        bronze_sources={
            "events": "mlb_statsapi",     # schedule / venue / roof / probables / lineups
            "pbp": "statcast",            # pitch-by-pitch batted-ball (pybaseball / Savant)
            "weather": "open_meteo",      # first-pitch temp/wind (+ ERA5 archive for backfill)
        },
        league_avg={
            "csw_rate":      0.281,
            "whiff_rate":    0.254,
            "barrel_rate":   0.078,
            "avg_xwoba":     0.312,
            "avg_exit_velo": 88.5,
            "zone_rate":     0.475,
            "chase_rate":    0.295,
        },
    ),
    # NFL — football's same-day edge is inactives + line movement, not park/weather.
    # nfl_data_py was ARCHIVED (Sep 2025); the module targets `nflreadpy`. Inactives
    # have no free contractual feed → `espn` scrape (build defensively + monitor);
    # SportsDataIO DeclaredInactive is deferred paid insurance only.
    "nfl": SportConfig(
        sport_key="nfl",
        display_name="NFL",
        entity_noun="passer",
        target="pass_td_scored",
        entity_grain="passer_id",
        event_grain="game_id",
        build_table="passer_matchup_features",
        wide_table="nfl_matchup_features",
        odds_api_sport="americanfootball_nfl",
        odds_api_market="player_pass_tds",
        side_yes_label="Over",
        bronze_sources={
            "pbp": "nflverse",            # via nflreadpy: pbp, box, snaps, settlement
            "rosters": "nflverse",
            "schedule": "nflverse",       # includes roof/surface → dome flag
            "inactives": "espn",          # live actives ~pre-kick (unofficial; monitor)
            "weather": "open_meteo",      # outdoor/wind games only
        },
        league_avg={},                    # populated when NFL feature block lands
    ),
    # CFB — reuses the NFL module shape. No official inactives mechanism + heavy
    # transfer-portal roster churn → coverage gaps are FLAGGED, not silently dropped.
    # CFBD free tier (1k calls/mo) is too small for a live Saturday slate → Tier 3.
    "cfb": SportConfig(
        sport_key="cfb",
        display_name="CFB",
        entity_noun="passer",
        target="pass_td_scored",
        entity_grain="passer_id",
        event_grain="game_id",
        build_table="passer_matchup_features",
        wide_table="cfb_matchup_features",
        odds_api_sport="americanfootball_ncaaf",
        odds_api_market="player_pass_tds",
        side_yes_label="Over",
        bronze_sources={
            "pbp": "collegefootballdata",
            "rosters": "collegefootballdata",
            "venues": "collegefootballdata",   # lat/lon + dome flag → weather join
            "schedule": "collegefootballdata",
            "weather": "open_meteo",
        },
        league_avg={},
    ),
}

DEFAULT_SPORT = "mlb"


def get_sport(sport_key: str = DEFAULT_SPORT) -> SportConfig:
    """Look up a sport config; raises KeyError with the known keys on a typo."""
    try:
        return REGISTRY[sport_key]
    except KeyError:
        raise KeyError(
            f"Unknown sport_key {sport_key!r}; registered sports: {sorted(REGISTRY)}"
        ) from None


def registered_sports() -> list[str]:
    return sorted(REGISTRY)
