"""NFL per-sport ingestion module (stub).

The NFL feature module is the second sport registered against the
sport-agnostic core (see pipeline/sports.py `REGISTRY['nfl']`). Football's
same-day edge is INACTIVES + LINE MOVEMENT, not park/weather — so the
dominant adapter here is the live inactives feed, not a batted-ball feed.

Verified source_roots (see pipeline.sports.get_sport('nfl').bronze_sources):
    nflverse   — via nflreadpy (nfl_data_py is ARCHIVED as of Sep 2025): pbp,
                 player box, snap counts, rosters, schedule/venue, settlement.
    espn       — live inactives/actives (~pre-kick, unofficial → monitor).
    open_meteo — outdoor/wind games only.

These are STUBS: the fetch/parse split and source contracts are defined so the
core seam is proven; implementations raise NotImplementedError.
"""
