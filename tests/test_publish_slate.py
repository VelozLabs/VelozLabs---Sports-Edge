"""Tests for pipeline/publish_slate.py — the slate publish CLI orchestration.

Exercises the real DuckDB read path (in-memory) + transforms against a recording
StorageBackend, so no live Supabase is needed. Verifies FK-safe publish ordering
(event + entity BEFORE layer_score) and the schedule/players → core transforms.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from pipeline import publish_slate
from pipeline.storage import StorageBackend


class RecordingBackend(StorageBackend):
    def __init__(self):
        self.calls = []            # ordered list of (table, natural_key, sport, rows)

    def write_parquet(self, df, path):  # pragma: no cover - unused here
        return str(path)

    def publish_table(self, df, table, natural_key, sport):
        self.calls.append((table, tuple(natural_key), sport, len(df)))
        return len(df)


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("""
        CREATE TABLE schedule AS SELECT * FROM (VALUES
            (776001, DATE '2026-07-10', TIMESTAMP '2026-07-10 23:05:00', 'night',
             'Final', 15, 'Citizens Bank Park', 'PHI', 'ATL'),
            (776002, DATE '2026-07-10', TIMESTAMP '2026-07-10 20:10:00', 'night',
             'Final', 3,  'Yankee Stadium', 'NYY', 'BOS')
        ) AS t(game_pk, game_date, game_datetime_utc, day_night, status,
               venue_id, venue_name, home_team_abbr, away_team_abbr)
    """)
    c.execute("""
        CREATE TABLE players AS SELECT * FROM (VALUES
            (11, 'Batter #11', 'L'),
            (22, 'Batter #22', 'R'),
            (99, 'Pitcher #99', 'R')
        ) AS t(player_id, full_name, hand)
    """)
    # wide matchup: keys + target + quarantined + two registered features
    c.execute("""
        CREATE TABLE batter_matchup_features AS SELECT * FROM (VALUES
            (11, 776001, DATE '2026-07-10', 1, 4, 0.045, 112.0),
            (22, 776002, DATE '2026-07-10', 0, 3, 0.030, 117.0)
        ) AS t(batter_id, game_pk, game_date, hr_hit, pa_this_game,
               b_hr_per_pa_30d, park_hr_factor)
    """)
    yield c
    c.close()


def test_transform_events_maps_to_core(con):
    ev = publish_slate.transform_events(con.execute("SELECT * FROM schedule").df(), "mlb")
    assert set(["sport", "event_id", "game_date", "home_team", "away_team",
                "venue_name", "status", "source_root"]).issubset(ev.columns)
    assert ev["sport"].unique().tolist() == ["mlb"]
    assert sorted(ev["event_id"]) == [776001, 776002]
    assert ev.loc[ev.event_id == 776001, "home_team"].iloc[0] == "PHI"
    assert ev["source_root"].unique().tolist() == ["mlb_statsapi"]


def test_transform_entities_no_fabricated_handedness(con):
    en = publish_slate.transform_entities(con.execute("SELECT * FROM players").df(), "mlb")
    assert sorted(en["entity_id"]) == [11, 22, 99]
    assert en["entity_type"].unique().tolist() == ["player"]
    # bats/throws are NOT fabricated from the single `hand` column
    assert "bats" not in en.columns and "throws" not in en.columns


def test_publish_all_fk_safe_order_and_counts(con):
    backend = RecordingBackend()
    summary = publish_slate.publish_all(con, "mlb", backend)

    tables = [c[0] for c in backend.calls]
    # event + entity (FK parents) must precede layer_score
    assert tables.index("event") < tables.index("layer_score")
    assert tables.index("entity") < tables.index("layer_score")
    # wide table before the tall table derived from it
    assert tables.index("mlb_matchup_features") < tables.index("layer_score")

    by_table = {c[0]: c for c in backend.calls}
    assert by_table["event"][1] == ("sport", "event_id")
    assert by_table["entity"][1] == ("sport", "entity_id")
    assert by_table["mlb_matchup_features"][1] == ("batter_id", "game_pk")
    assert by_table["layer_score"][1] == (
        "sport", "event_id", "entity_id", "layer", "feature_name")

    assert summary["event"] == 2
    assert summary["entity"] == 3
    assert summary["mlb_matchup_features"] == 2
    # 2 wide rows × 2 registered features (hr_hit/pa_this_game excluded) = 4 tall rows
    assert summary["layer_score"] == 4


def test_publish_all_skips_missing_source_tables():
    c = duckdb.connect(":memory:")
    try:
        backend = RecordingBackend()
        summary = publish_slate.publish_all(c, "mlb", backend)
    finally:
        c.close()
    assert summary == {}          # nothing present → nothing published, no error
