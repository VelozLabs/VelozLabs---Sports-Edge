"""
pipeline/ingestion/nfl/inactives_ingestion.py
==============================================
NFL live inactives / actives ingestion via the ESPN hidden API (STUB).

This is the #1 same-day NFL edge and the WEAK LINK in sourcing: there is no
free CONTRACTUAL 90-minute inactives feed. nflverse `load_injuries()` is the
nightly practice report (Q/D/O), not the official pre-kick inactive list, and
is itself ESPN-derived. So live actives are scraped from ESPN's game
summary/boxscore (per-player active/inactive status appears ~pre-kick),
UNOFFICIAL and timing-not-guaranteed → build defensively with monitoring;
SportsDataIO `DeclaredInactive` is deferred paid insurance.

source_root: 'espn'  (see pipeline.sports.get_sport('nfl').bronze_sources)

Feeds the generic core `lineup_confirmed` table (the LINEUP_RELEASE / actives
availability gate): a player marked inactive is a scratch; confirmed actives
gate tiering (confirmed-actives-only guardrail).

Coverage-gap hygiene (project rule "flag, don't drop silently"): when ESPN has
not yet populated actives for a game at snapshot time, emit a COVERAGE_GAP
notification (pipeline.notify) rather than silently under-sampling the slate.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SOURCE_ROOT = "espn"

# ESPN hidden API (no key/auth; be polite on rate; unofficial — may change).
ESPN_NFL_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
)


def fetch_actives(event_id: str, session: object | None = None) -> object:
    """Pull the ESPN game summary/boxscore for one event (raw). STUB.

    Intended: GET {ESPN_NFL_SUMMARY_URL}?event={event_id}; cache raw to
    data/bronze/espn/actives/date=*/ before parsing (cache-first, like the MLB
    adapters). `session` is injectable so tests run on fixtures with no network.
    """
    raise NotImplementedError(
        "NFL inactives ingestion is stubbed. Implement a cache-first ESPN summary "
        "GET; parse per-player active/inactive with parse_actives()."
    )


def parse_actives(raw: object) -> "list[dict]":
    """Flatten raw ESPN summary → core `lineup_confirmed` rows (pure). STUB.

    Target row shape (generic core `lineup_confirmed`):
        {sport:'nfl', event_id, entity_id, lineup_slot:None,
         status:'confirmed'|'scratched', confirmed_ts, game_date,
         source_root:'espn'}
    On missing/empty actives for a scheduled game → caller flags COVERAGE_GAP.
    """
    raise NotImplementedError("parse_actives() not yet implemented for NFL.")
