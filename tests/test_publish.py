"""
tests/test_publish.py
======================
Offline tests for pipeline/publish.py — the Gold-DataFrame -> core-Postgres
orchestration seam. No live Supabase: a `RecordingBackend` stands in for
`StorageBackend` and just records what it was asked to publish.

Covers:
    * layer_for() prefix classification (b_/p_/bp_/park_ + context columns).
    * build_layer_score(): melts only REGISTRY-registered feature columns,
      excludes keys/target/quarantined columns, assigns layers correctly,
      applies the is_night_before recorded_at rule, and preserves NaN as null.
    * build_layer_score() raises ValueError on an unregistered feature column.
    * Each publish_* helper uses the documented natural_key / table and
      returns the row count the backend reports.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from pipeline.publish import (
    build_layer_score,
    layer_for,
    publish_bets,
    publish_entities,
    publish_events,
    publish_layer_score,
    publish_matchup_wide,
    publish_prop_prices,
)
from pipeline.sports import get_sport
from pipeline.storage import StorageBackend


class RecordingBackend(StorageBackend):
    """Records every publish_table call instead of touching a live database."""

    def __init__(self):
        self.calls: list[dict] = []

    def write_parquet(self, df, path):
        return str(path)

    def publish_table(self, df, table, natural_key, sport):
        self.calls.append({
            "table": table,
            "natural_key": list(natural_key),
            "sport": sport,
            "df": df,
        })
        return len(df)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def wide_df() -> pd.DataFrame:
    """Small MLB-shaped wide matchup frame with registered features, the
    target, and one quarantined same-game column, plus one NaN feature value."""
    return pd.DataFrame({
        "batter_id": [1, 2, 3],
        "game_pk": [100, 100, 101],
        "game_date": pd.to_datetime(["2026-07-10", "2026-07-10", "2026-07-11"]),
        "hr_hit": [1, 0, 0],
        "pa_this_game": [4, 3, 5],                      # quarantined, not a feature
        "b_hr_per_pa_30d": [0.05, np.nan, 0.03],         # NaN must survive
        "p_hr_per_pa_allowed_30d": [0.04, 0.06, 0.05],
        "bp_hr_per_pa_allowed_30d": [0.03, 0.04, 0.035],
        "park_hr_factor": [1.1, 0.95, 1.0],
        "is_home": [1, 0, 1],
        "temp_f": [75.0, 68.0, 80.0],
    })


REGISTERED_FEATURES = {
    "b_hr_per_pa_30d",
    "p_hr_per_pa_allowed_30d",
    "bp_hr_per_pa_allowed_30d",
    "park_hr_factor",
    "is_home",
    "temp_f",
}


# ── layer_for ────────────────────────────────────────────────────────────────

class TestLayerFor:

    def test_form_prefix(self):
        assert layer_for("b_hr_per_pa_30d") == "form"

    def test_opponent_prefix(self):
        assert layer_for("p_hr_per_pa_allowed_30d") == "opponent"

    def test_bullpen_prefix_not_swallowed_by_batter_prefix(self):
        # bp_ must not be misclassified as b_'s 'form' layer.
        assert layer_for("bp_hr_per_pa_allowed_30d") == "bullpen"

    def test_park_prefix(self):
        assert layer_for("park_hr_factor") == "park"

    @pytest.mark.parametrize("col", ["is_home", "is_night", "temp_f", "wind_out_mph", "roof"])
    def test_context_columns(self, col):
        assert layer_for(col) == "context"

    def test_unknown_defaults_to_context(self):
        assert layer_for("some_new_context_col") == "context"


# ── build_layer_score ───────────────────────────────────────────────────────

class TestBuildLayerScore:

    def test_melts_only_registered_features(self, wide_df):
        long_df = build_layer_score(wide_df, "mlb", source_root="duckdb_gold_publish")
        assert set(long_df["feature_name"].unique()) == REGISTERED_FEATURES
        # 3 wide rows x 6 features = 18 long rows
        assert len(long_df) == 3 * len(REGISTERED_FEATURES)

    def test_excludes_target_and_quarantined_and_keys(self, wide_df):
        long_df = build_layer_score(wide_df, "mlb", source_root="duckdb_gold_publish")
        names = set(long_df["feature_name"].unique())
        assert "hr_hit" not in names
        assert "pa_this_game" not in names
        assert "batter_id" not in names
        assert "game_pk" not in names
        assert "game_date" not in names

    def test_output_columns(self, wide_df):
        long_df = build_layer_score(wide_df, "mlb", source_root="duckdb_gold_publish")
        expected = {
            "sport", "event_id", "entity_id", "layer", "feature_name",
            "value", "game_date", "recorded_at", "source_root",
        }
        assert expected.issubset(set(long_df.columns))
        assert (long_df["sport"] == "mlb").all()
        assert (long_df["source_root"] == "duckdb_gold_publish").all()

    def test_layer_assignment_per_prefix(self, wide_df):
        long_df = build_layer_score(wide_df, "mlb", source_root="duckdb_gold_publish")
        by_feature = long_df.drop_duplicates("feature_name").set_index("feature_name")["layer"]
        assert by_feature["b_hr_per_pa_30d"] == "form"
        assert by_feature["p_hr_per_pa_allowed_30d"] == "opponent"
        assert by_feature["bp_hr_per_pa_allowed_30d"] == "bullpen"
        assert by_feature["park_hr_factor"] == "park"
        assert by_feature["is_home"] == "context"
        assert by_feature["temp_f"] == "context"

    def test_entity_and_event_id_mapped_from_grain(self, wide_df):
        long_df = build_layer_score(wide_df, "mlb", source_root="duckdb_gold_publish")
        # batter_id / game_pk (mlb's entity_grain/event_grain) map onto entity_id/event_id
        row = long_df[(long_df["feature_name"] == "b_hr_per_pa_30d")].sort_values("entity_id")
        assert list(row["entity_id"]) == [1, 2, 3]
        assert list(row["event_id"]) == [100, 100, 101]

    def test_recorded_at_same_day_by_default(self, wide_df):
        long_df = build_layer_score(wide_df, "mlb", source_root="duckdb_gold_publish")
        game_date = pd.to_datetime(long_df["game_date"])
        recorded_at = pd.to_datetime(long_df["recorded_at"])
        assert (recorded_at == game_date).all()

    def test_recorded_at_night_before(self, wide_df):
        long_df = build_layer_score(
            wide_df, "mlb", source_root="open_meteo", is_night_before=True
        )
        game_date = pd.to_datetime(long_df["game_date"])
        recorded_at = pd.to_datetime(long_df["recorded_at"])
        assert (recorded_at == game_date - pd.Timedelta(days=1)).all()

    def test_nan_preserved_as_null(self, wide_df):
        long_df = build_layer_score(wide_df, "mlb", source_root="duckdb_gold_publish")
        b_rows = long_df[long_df["feature_name"] == "b_hr_per_pa_30d"].sort_values("entity_id")
        values = list(b_rows["value"])
        assert math.isnan(values[1])  # batter_id 2 had NaN
        assert values[0] == pytest.approx(0.05)
        assert values[2] == pytest.approx(0.03)
        # not silently dropped: still 3 rows for this feature (one row per wide row)
        assert len(b_rows) == 3

    def test_raises_on_unregistered_feature_column(self, wide_df):
        bad_df = wide_df.copy()
        bad_df["b_bogus_stat"] = [1.0, 2.0, 3.0]
        with pytest.raises(ValueError, match="b_bogus_stat"):
            build_layer_score(bad_df, "mlb", source_root="duckdb_gold_publish")


# ── publish_* helpers ────────────────────────────────────────────────────────

class TestPublishHelpers:

    def test_publish_events_key_and_count(self):
        backend = RecordingBackend()
        df = pd.DataFrame({"sport": ["mlb"], "event_id": [100], "game_date": ["2026-07-10"]})
        n = publish_events(df, backend, "mlb")
        assert n == 1
        call = backend.calls[-1]
        assert call["table"] == "event"
        assert call["natural_key"] == ["sport", "event_id"]
        assert call["sport"] == "mlb"

    def test_publish_entities_key_and_count(self):
        backend = RecordingBackend()
        df = pd.DataFrame({"sport": ["mlb"] * 2, "entity_id": [1, 2]})
        n = publish_entities(df, backend, "mlb")
        assert n == 2
        call = backend.calls[-1]
        assert call["table"] == "entity"
        assert call["natural_key"] == ["sport", "entity_id"]

    def test_publish_matchup_wide_uses_sport_config(self, wide_df):
        backend = RecordingBackend()
        n = publish_matchup_wide(wide_df, backend, "mlb")
        cfg = get_sport("mlb")
        assert n == len(wide_df)
        call = backend.calls[-1]
        assert call["table"] == cfg.wide_table == "mlb_matchup_features"
        assert call["natural_key"] == [cfg.entity_grain, cfg.event_grain] == ["batter_id", "game_pk"]
        assert call["sport"] == "mlb"

    def test_publish_matchup_wide_generalizes_to_other_sports(self):
        backend = RecordingBackend()
        cfg = get_sport("nfl")
        df = pd.DataFrame({cfg.entity_grain: [1], cfg.event_grain: [200]})
        publish_matchup_wide(df, backend, "nfl")
        call = backend.calls[-1]
        assert call["table"] == "nfl_matchup_features"
        assert call["natural_key"] == ["passer_id", "game_id"]

    def test_publish_prop_prices_key_and_count(self):
        backend = RecordingBackend()
        df = pd.DataFrame({
            "prop_id": [1, 1],
            "book": ["draftkings", "fanduel"],
            "side": ["Yes", "Yes"],
            "snapshot_ts": ["2026-07-10T12:00:00Z", "2026-07-10T12:00:00Z"],
        })
        n = publish_prop_prices(df, backend, "mlb")
        assert n == 2
        call = backend.calls[-1]
        assert call["table"] == "prop_price"
        assert call["natural_key"] == ["prop_id", "book", "side", "snapshot_ts"]

    def test_publish_bets_key_and_count(self):
        backend = RecordingBackend()
        df = pd.DataFrame({
            "prop_id": [1],
            "book": ["draftkings"],
            "snapshot_ts": ["2026-07-10T12:00:00Z"],
            "model_p": [0.12],
        })
        n = publish_bets(df, backend, "mlb")
        assert n == 1
        call = backend.calls[-1]
        assert call["table"] == "bet"
        assert call["natural_key"] == ["prop_id", "book", "snapshot_ts", "model_p"]

    def test_publish_layer_score_key_and_count(self, wide_df):
        backend = RecordingBackend()
        long_df = build_layer_score(wide_df, "mlb", source_root="duckdb_gold_publish")
        n = publish_layer_score(long_df, backend, "mlb")
        assert n == len(long_df)
        call = backend.calls[-1]
        assert call["table"] == "layer_score"
        assert call["natural_key"] == ["sport", "event_id", "entity_id", "layer", "feature_name"]
