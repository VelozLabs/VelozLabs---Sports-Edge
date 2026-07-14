"""
tests/test_layer_backtest.py
==============================
Layer-isolated backtesting tests against synthetic `layer_score` / `result`
DataFrames (no DB, fully offline).

Scenario: sport='mlb', 40 (event_id, entity_id) rows.
    - layer 'form' / feature 'form_streak': a monotonically increasing value
      that deterministically drives outcome (hit iff value > 0) — this is
      the strongly predictive SCORING-layer signal.
    - layer 'context' / feature 'wind_out_mph': a seeded-random value with no
      relationship to outcome — the near-random ENVIRONMENT-layer signal,
      with 5 injected NaNs to exercise coverage.
    - a handful of 'nfl' rows are mixed into both frames to prove the sport
      filter actually excludes them.

Assertions pin down: one row per (layer, feature_name); coverage math;
'void' outcomes excluded from corr/AUC but still counted in n; the
predictive feature beating the random one on |corr| and AUC; the
environment/scoring layer_kind split; sport filtering; layer_summary
aggregation; and layer_roi being a no-op without priced odds columns but
returning a real number (reusing models.evaluate.flat_stake_roi) when they
are present.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.layer_backtest import grade_layers, layer_roi, layer_summary

N = 40


def _mlb_layer_df() -> pd.DataFrame:
    entity_ids = list(range(1, N + 1))
    event_ids = list(range(1000, 1000 + N))

    # 'form': strongly predictive — monotonic, drives outcome deterministically.
    form_value = np.linspace(-3.0, 3.0, N)

    # 'context': seeded-random, unrelated to outcome ordering.
    rng = np.random.RandomState(42)
    context_value = rng.uniform(-1.0, 1.0, N).astype(float)
    nan_idx = [2, 9, 17, 25, 33]  # 5 NaNs -> coverage 35/40
    context_value[nan_idx] = np.nan

    rows = []
    for i in range(N):
        rows.append({
            "sport": "mlb", "event_id": event_ids[i], "entity_id": entity_ids[i],
            "layer": "form", "feature_name": "form_streak", "value": form_value[i],
            "game_date": "2024-06-01", "recorded_at": "2024-05-31T00:00:00Z",
            "source_root": "duckdb_gold_publish",
        })
        rows.append({
            "sport": "mlb", "event_id": event_ids[i], "entity_id": entity_ids[i],
            "layer": "context", "feature_name": "wind_out_mph", "value": context_value[i],
            "game_date": "2024-06-01", "recorded_at": "2024-05-31T00:00:00Z",
            "source_root": "open_meteo",
        })

    # decoy rows for a different sport — must be excluded when sport='mlb'.
    for i in range(5):
        rows.append({
            "sport": "nfl", "event_id": 9000 + i, "entity_id": 9000 + i,
            "layer": "form", "feature_name": "form_streak", "value": 1.0,
            "game_date": "2024-09-01", "recorded_at": "2024-08-31T00:00:00Z",
            "source_root": "nflverse",
        })

    return pd.DataFrame(rows)


def _mlb_result_df() -> pd.DataFrame:
    entity_ids = list(range(1, N + 1))
    event_ids = list(range(1000, 1000 + N))
    form_value = np.linspace(-3.0, 3.0, N)
    outcome = np.where(form_value > 0, "hit", "miss")

    rows = []
    for i in range(N):
        rows.append({
            "sport": "mlb", "event_id": event_ids[i], "entity_id": entity_ids[i],
            "market": "batter_home_runs",
            "actual_value": 1.0 if outcome[i] == "hit" else 0.0,
            "outcome": outcome[i], "game_date": "2024-06-01",
        })

    for i in range(5):
        rows.append({
            "sport": "nfl", "event_id": 9000 + i, "entity_id": 9000 + i,
            "market": "player_pass_tds", "actual_value": 1.0,
            "outcome": "hit", "game_date": "2024-09-01",
        })

    return pd.DataFrame(rows)


@pytest.fixture
def layer_df():
    return _mlb_layer_df()


@pytest.fixture
def result_df():
    return _mlb_result_df()


@pytest.fixture
def graded(layer_df, result_df):
    return grade_layers(layer_df, result_df, "mlb")


# ── grade_layers ──────────────────────────────────────────────────────────

def test_one_row_per_layer_feature(graded):
    pairs = set(zip(graded["layer"], graded["feature_name"]))
    assert pairs == {("form", "form_streak"), ("context", "wind_out_mph")}
    assert len(graded) == 2


def test_coverage_computed_correctly(graded):
    context_row = graded.loc[graded["layer"] == "context"].iloc[0]
    form_row = graded.loc[graded["layer"] == "form"].iloc[0]
    assert context_row["coverage"] == pytest.approx((N - 5) / N)
    assert form_row["coverage"] == pytest.approx(1.0)


def test_predictive_feature_beats_random_feature(graded):
    form_row = graded.loc[graded["layer"] == "form"].iloc[0]
    context_row = graded.loc[graded["layer"] == "context"].iloc[0]

    assert abs(form_row["corr"]) > abs(context_row["corr"])
    assert form_row["auc"] > context_row["auc"]
    # the constructed signal is strongly, not just marginally, predictive
    assert abs(form_row["corr"]) > 0.7
    assert form_row["auc"] > 0.9


def test_layer_kind_environment_scoring_split(graded):
    kinds = graded.set_index("layer")["layer_kind"].to_dict()
    assert kinds == {"form": "scoring", "context": "environment"}


def test_sport_filtering_excludes_other_sports(layer_df, result_df):
    graded_mlb = grade_layers(layer_df, result_df, "mlb")
    # the 5 'nfl' decoy rows must not leak into the 'mlb' grade
    form_row = graded_mlb.loc[graded_mlb["layer"] == "form"].iloc[0]
    assert form_row["n"] == N

    graded_nfl = grade_layers(layer_df, result_df, "nfl")
    assert len(graded_nfl) == 1
    assert graded_nfl.iloc[0]["n"] == 5
    assert (graded_nfl["sport"] == "nfl").all()


def test_void_outcomes_excluded_from_corr_and_auc_but_counted_in_n():
    layer_df = pd.DataFrame([
        {"sport": "mlb", "event_id": 1, "entity_id": 1, "layer": "form",
         "feature_name": "f", "value": 1.0, "game_date": "2024-06-01",
         "recorded_at": "2024-05-31T00:00:00Z", "source_root": "duckdb_gold_publish"},
        {"sport": "mlb", "event_id": 2, "entity_id": 2, "layer": "form",
         "feature_name": "f", "value": 2.0, "game_date": "2024-06-01",
         "recorded_at": "2024-05-31T00:00:00Z", "source_root": "duckdb_gold_publish"},
        {"sport": "mlb", "event_id": 3, "entity_id": 3, "layer": "form",
         "feature_name": "f", "value": 3.0, "game_date": "2024-06-01",
         "recorded_at": "2024-05-31T00:00:00Z", "source_root": "duckdb_gold_publish"},
        {"sport": "mlb", "event_id": 4, "entity_id": 4, "layer": "form",
         "feature_name": "f", "value": 4.0, "game_date": "2024-06-01",
         "recorded_at": "2024-05-31T00:00:00Z", "source_root": "duckdb_gold_publish"},
    ])
    result_df = pd.DataFrame([
        {"sport": "mlb", "event_id": 1, "entity_id": 1, "market": "m",
         "actual_value": 1.0, "outcome": "hit", "game_date": "2024-06-01"},
        {"sport": "mlb", "event_id": 2, "entity_id": 2, "market": "m",
         "actual_value": 0.0, "outcome": "miss", "game_date": "2024-06-01"},
        {"sport": "mlb", "event_id": 3, "entity_id": 3, "market": "m",
         "actual_value": None, "outcome": "void", "game_date": "2024-06-01"},
        {"sport": "mlb", "event_id": 4, "entity_id": 4, "market": "m",
         "actual_value": 1.0, "outcome": "hit", "game_date": "2024-06-01"},
    ])

    graded_df = grade_layers(layer_df, result_df, "mlb")
    assert len(graded_df) == 1
    row = graded_df.iloc[0]

    assert row["n"] == 4  # the void row still counts toward n
    expected_corr = round(
        pd.Series([1.0, 2.0, 4.0]).corr(pd.Series([1.0, 0.0, 1.0])), 5
    )
    assert row["corr"] == pytest.approx(expected_corr)


# ── layer_summary ─────────────────────────────────────────────────────────

def test_layer_summary_aggregates_and_keeps_kind_split(graded):
    summary = layer_summary(graded)
    assert len(summary) == 2
    assert set(summary["layer_kind"]) == {"environment", "scoring"}

    form_summary = summary.loc[summary["layer"] == "form"].iloc[0]
    context_summary = summary.loc[summary["layer"] == "context"].iloc[0]
    assert form_summary["n_features"] == 1
    assert context_summary["n_features"] == 1
    # predictiveness ordering should carry through the aggregation
    assert form_summary["mean_abs_corr"] > context_summary["mean_abs_corr"]


def test_layer_summary_empty_input_returns_empty_df():
    empty = grade_layers(pd.DataFrame(columns=[
        "sport", "event_id", "entity_id", "layer", "feature_name", "value",
        "game_date", "recorded_at", "source_root",
    ]), pd.DataFrame(columns=[
        "sport", "event_id", "entity_id", "market", "actual_value", "outcome", "game_date",
    ]), "mlb")
    summary = layer_summary(empty)
    assert summary.empty


# ── layer_roi (optional, price-gated) ──────────────────────────────────────

def test_layer_roi_skipped_without_odds_columns(layer_df, result_df):
    # result_df has no model_p/market_p/decimal_odds -> no price, no ROI claim
    assert "model_p" not in result_df.columns
    result = layer_roi(layer_df, result_df, "mlb")
    assert result is None


def test_layer_roi_returns_number_when_odds_present():
    layer_df = pd.DataFrame([
        {"sport": "mlb", "event_id": i, "entity_id": i, "layer": "form",
         "feature_name": "f", "value": float(i), "game_date": "2024-06-01",
         "recorded_at": "2024-05-31T00:00:00Z", "source_root": "duckdb_gold_publish"}
        for i in range(1, 5)
    ])
    result_df = pd.DataFrame({
        "sport": ["mlb"] * 4,
        "event_id": [1, 2, 3, 4],
        "entity_id": [1, 2, 3, 4],
        "market": ["batter_home_runs"] * 4,
        "actual_value": [1.0, 0.0, 1.0, 0.0],
        "outcome": ["hit", "miss", "hit", "miss"],
        "game_date": ["2024-06-01"] * 4,
        "model_p": [0.30, 0.10, 0.25, 0.05],
        "market_p": [0.20, 0.15, 0.10, 0.10],
        "decimal_odds": [3.0, 5.0, 4.0, 8.0],
    })

    result = layer_roi(layer_df, result_df, "mlb", edge_threshold=0.02)
    assert result is not None
    assert isinstance(result["roi"], float)
    assert result["bets"] == 2  # rows with model_p - market_p > 0.02: idx 0 and 2
