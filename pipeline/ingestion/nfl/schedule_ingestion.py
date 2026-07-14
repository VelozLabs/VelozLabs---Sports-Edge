"""
pipeline/ingestion/nfl/schedule_ingestion.py
=============================================
NFL schedule / rosters / play-by-play ingestion via nflverse (STUB).

Mirrors the MLB ingestion idiom (fetch and parse are SEPARATE pure functions
so tests run on recorded fixtures with zero network; raw responses cached to
Bronze before parsing). Consumed through `nflreadpy` — NOT the archived
`nfl_data_py` package.

source_root: 'nflverse'  (see pipeline.sports.get_sport('nfl').bronze_sources)

Feeds the generic core:
    event  ← schedules (game_id, game_date, kickoff, venue, roof → dome flag)
    entity ← rosters   (passer_id, full_name, position)
    settlement / features ← pbp + player box

Independence note: nflverse `load_injuries()` is the nightly practice report and
is now ESPN-derived — it is NOT an independent source_root from `espn`. Live
inactives come from the espn adapter, not here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SOURCE_ROOT = "nflverse"


def fetch_schedule(season: int) -> object:
    """Pull the nflverse schedule for a season (raw). STUB.

    Intended: `import nflreadpy as nfl; nfl.load_schedules([season])` (lazy
    import so the core imports without the dep). Cache raw to
    data/bronze/nflverse/schedule/season=<season>/ before parsing.
    """
    raise NotImplementedError(
        "NFL schedule ingestion is stubbed. Implement via nflreadpy.load_schedules "
        "([season]); cache raw to Bronze; then parse_schedule()."
    )


def parse_schedule(raw: object) -> "list[dict]":
    """Flatten raw nflverse schedule → core `event` rows (pure). STUB.

    Target row shape (generic core `event`):
        {sport:'nfl', event_id:<game_id>, game_date, commence_ts,
         home_team, away_team, venue_name, status, source_root:'nflverse'}
    Set a dome/roof flag for the weather join (weather = outdoor/wind only).
    """
    raise NotImplementedError("parse_schedule() not yet implemented for NFL.")


def fetch_rosters(season: int) -> object:
    """Pull nflverse weekly rosters (raw) for name-matching + entity rows. STUB."""
    raise NotImplementedError(
        "NFL roster ingestion is stubbed. Implement via nflreadpy.load_rosters "
        "([season]) / load_ff_playerids() for the fuzzy-match crosswalk."
    )


def parse_rosters(raw: object) -> "list[dict]":
    """Flatten raw rosters → core `entity` rows (pure). STUB.

    Target: {sport:'nfl', entity_id:<passer_id>, full_name, entity_type:'qb',
             source_root:'nflverse'}
    """
    raise NotImplementedError("parse_rosters() not yet implemented for NFL.")
