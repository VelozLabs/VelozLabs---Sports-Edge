"""
pipeline/publish_slate.py
=========================
CLI entrypoint the daily GitHub Actions workflow calls: read the DuckDB Gold/
Silver tables for a slate and publish them to Supabase via the storage seam.

    python -m pipeline.publish_slate --sport mlb --date 2026-07-10

Publish order is FK-safe (the core schema's foreign keys require parents first):

    event  ←  schedule          (sport, event_id)                 -- FK parent
    entity ←  players           (sport, entity_id)                -- FK parent
    <sport>_matchup_features ← build_table   (entity_grain, event_grain)
    layer_score ← derived from the wide frame (sport,event_id,entity_id,layer,feature_name)

Each publish delegates to pipeline/publish.py (natural keys match the migration
UNIQUE constraints) through a StorageBackend (pipeline/storage.py). Backend is
chosen by VOSS_STORAGE; the Supabase backend needs the pooler DSN (SUPABASE_DB_URL,
port 6543). A missing source table is logged/notified and skipped, not fatal.

NOTE: the wide serving table `<sport>_matchup_features` must exist in Postgres
(migration 0002) and `event`/`entity` must be published first, or the wide/
layer_score FK inserts will fail — which is exactly the FK-safe order below.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from pipeline import publish
from pipeline.config import DUCKDB_PATH
from pipeline.notify import PUBLISH_FAILED, SLATE_PUBLISHED, notify_error, notify_info
from pipeline.sports import get_sport

logger = logging.getLogger(__name__)

EVENT_SOURCE_ROOT = "mlb_statsapi"        # schedule/event provenance
ENTITY_SOURCE_ROOT = "mlb_statsapi"       # roster/entity provenance
LAYER_SOURCE_ROOT = "duckdb_gold_publish"  # Gold-published tall rows


def _table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()[0] > 0


def transform_events(schedule_df, sport: str):
    """`schedule` (SCHEDULE_COLS) → core `event` rows."""
    df = schedule_df.rename(columns={
        "game_pk": "event_id",
        "game_datetime_utc": "commence_ts",
        "home_team_abbr": "home_team",
        "away_team_abbr": "away_team",
    })
    df["sport"] = sport
    df["source_root"] = EVENT_SOURCE_ROOT
    cols = ["sport", "event_id", "game_date", "commence_ts",
            "home_team", "away_team", "venue_name", "status", "source_root"]
    return df[[c for c in cols if c in df.columns]].drop_duplicates("event_id")


def transform_entities(players_df, sport: str):
    """`players` (player_id, full_name, hand) → core `entity` rows.

    Handedness on the collapsed `players` table is a single `hand` column; the
    authoritative bats/throws live in the wide matchup features, so entity rows
    carry name + type only (enrichment is a follow-up), never fabricated splits.
    """
    df = players_df.rename(columns={"player_id": "entity_id"})
    df["sport"] = sport
    if "entity_type" not in df.columns:
        df["entity_type"] = "player"
    df["source_root"] = ENTITY_SOURCE_ROOT
    cols = ["sport", "entity_id", "full_name", "entity_type", "source_root"]
    return df[[c for c in cols if c in df.columns]].drop_duplicates("entity_id")


def publish_all(con, sport: str, storage, *, slate_date: str | None = None) -> dict:
    """Publish every present source table for `sport` in FK-safe order.

    Returns {logical_name: rows_published}. Raises on a genuine publish failure
    (so CI surfaces it + the Discord failure alert fires); missing SOURCE tables
    are skipped, not errors.
    """
    cfg = get_sport(sport)
    summary: dict[str, int] = {}

    # 1) event (FK parent) ← schedule
    if _table_exists(con, "schedule"):
        ev = transform_events(con.execute("SELECT * FROM schedule").df(), sport)
        summary["event"] = publish.publish_events(ev, storage, sport)
    else:
        logger.warning("no `schedule` table — skipping event publish (FK parents absent)")

    # 2) entity (FK parent) ← players
    if _table_exists(con, "players"):
        en = transform_entities(con.execute("SELECT * FROM players").df(), sport)
        summary["entity"] = publish.publish_entities(en, storage, sport)
    else:
        logger.warning("no `players` table — skipping entity publish (FK parents absent)")

    # 3) wide per-sport matchup table ← build_table
    if _table_exists(con, cfg.build_table):
        wide = con.execute(f"SELECT * FROM {cfg.build_table}").df()
        summary[cfg.wide_table] = publish.publish_matchup_wide(wide, storage, sport)

        # 4) tall layer_score ← derived from the same wide frame
        long_df = publish.build_layer_score(wide, sport, source_root=LAYER_SOURCE_ROOT)
        summary["layer_score"] = publish.publish_layer_score(long_df, storage, sport)
    else:
        logger.warning("no `%s` table — skipping wide + layer_score publish", cfg.build_table)

    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Publish a slate's Gold tables to the serving store.")
    ap.add_argument("--sport", default="mlb", help="sport key (mlb|nfl|cfb)")
    ap.add_argument("--date", default=str(date.today()), help="slate date YYYY-MM-DD")
    ap.add_argument("--duckdb", default=str(DUCKDB_PATH), help="path to the DuckDB file")
    args = ap.parse_args(argv)

    import duckdb  # lazy: keep module import cheap / offline-friendly
    from pipeline.storage import get_storage

    try:
        with duckdb.connect(args.duckdb, read_only=True) as con:
            storage = get_storage(con=con)   # 'local' no-op or 'supabase' via VOSS_STORAGE
            summary = publish_all(con, args.sport, storage, slate_date=args.date)
    except Exception as exc:
        notify_error(PUBLISH_FAILED, "Slate publish failed",
                     f"{args.sport} {args.date}: {exc}", sport=args.sport)
        logger.exception("publish_slate failed")
        return 1

    total = sum(summary.values())
    logger.info("Published slate %s %s: %s", args.sport, args.date, summary)
    notify_info(SLATE_PUBLISHED, f"Slate published: {args.sport} {args.date}",
                f"{total} rows across {len(summary)} tables", sport=args.sport, **summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
