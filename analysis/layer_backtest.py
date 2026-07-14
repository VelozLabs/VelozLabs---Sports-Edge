"""
analysis/layer_backtest.py
============================
Layer-isolated backtesting: grade each `layer_score` layer's predictiveness
independently, per sport, against `result` settlement rows.

The whole point of `layer_score` being a thin TALL table (sport, event_id,
entity_id, layer, feature_name, value, ...) is that layers can be swept ONE
AT A TIME across sports — "hold layer X fixed, sweep every other layer" (see
`idx_layer_score_layer` in db/migrations/0001_core_schema.up.sql). This module
is the DataFrame-in, function-level grading step on top of that table: no DB
connection is opened here, callers hand in `layer_score` and `result` already
materialized as DataFrames.

Guardrail this module encodes (docs/HR_PROP_PLAN.md): ENVIRONMENT layers
(park factor, weather/context — layers 'park' and 'context') are graded
SEPARATELY from SCORING layers (form / opponent / bullpen), because mixing
them would let a strong environment prior masquerade as skill in a scoring
layer. `LAYER_KIND` is the map that keeps that split explicit everywhere a
layer is reported.

    grade_layers  — per (layer, feature_name) predictiveness: n, coverage,
                    corr(value, outcome), AUC.
    layer_summary — aggregate grade_layers() output up to one row per layer,
                    still split by layer_kind, sorted by predictiveness.
    layer_roi     — OPTIONAL flat-stake ROI (reuses models.evaluate's exact
                    accounting) when model_p/market_p/decimal_odds are on
                    the result frame; gracefully skipped (returns None)
                    otherwise. Price-before-tiering: no odds, no ROI claim.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from models.evaluate import flat_stake_roi

logger = logging.getLogger(__name__)

# ENVIRONMENT layers are graded separately from SCORING layers (HR_PROP_PLAN
# guardrail). Any layer not listed here is reported as 'unknown' rather than
# silently folded into one side or the other.
LAYER_KIND: dict[str, str] = {
    "park": "environment",
    "context": "environment",
    "form": "scoring",
    "opponent": "scoring",
    "bullpen": "scoring",
}

# result.outcome CHECK (outcome IN ('hit','miss','void')) — 'void' graded out.
_OUTCOME_TO_BINARY = {"hit": 1, "miss": 0}

_ODDS_COLS = ("model_p", "market_p", "decimal_odds")


def _layer_kind(layer: str) -> str:
    kind = LAYER_KIND.get(layer)
    if kind is None:
        logger.warning("layer_backtest: layer %r not in LAYER_KIND, reporting as 'unknown'", layer)
        return "unknown"
    return kind


def _join(layer_df: pd.DataFrame, result_df: pd.DataFrame, sport: str) -> pd.DataFrame:
    """Filter both frames to `sport` and join on (event_id, entity_id)."""
    layers = layer_df.loc[layer_df["sport"] == sport].copy()
    results = result_df.loc[result_df["sport"] == sport].copy()
    results["y"] = results["outcome"].map(_OUTCOME_TO_BINARY)  # 'void' -> NaN
    joined = layers.merge(
        results,
        on=["event_id", "entity_id"],
        how="inner",
        suffixes=("", "_result"),
    )
    return joined


def _has_odds(df: pd.DataFrame) -> bool:
    return all(col in df.columns for col in _ODDS_COLS)


def grade_layers(
    layer_df: pd.DataFrame,
    result_df: pd.DataFrame,
    sport: str,
    *,
    edge_threshold: float = 0.0,
) -> pd.DataFrame:
    """
    Grade every (layer, feature_name) in `layer_df` for `sport` on how well it
    predicts `result_df.outcome`.

    Filters both inputs to `sport`, joins on (event_id, entity_id), then for
    each layer/feature computes:
        n        — joined row count
        coverage — fraction of non-null `value`
        corr     — Pearson corr(value, outcome) over hit/miss rows with a
                   non-null value ('void' outcomes and null values excluded)
        auc      — sklearn roc_auc_score(outcome, value), guarded for the
                   single-class / degenerate case (returned as NaN then)

    `layer_kind` (environment vs scoring, via LAYER_KIND) rides along on every
    row so layers are never accidentally compared across the guardrail split.

    If `result_df` carries model_p/market_p/decimal_odds (optional — the
    price-before-tiering columns), an extra `roi`/`roi_bets` column is added
    per layer/feature by reusing `models.evaluate.flat_stake_roi` at
    `edge_threshold`; otherwise those columns are simply omitted.
    """
    joined = _join(layer_df, result_df, sport)
    has_odds = _has_odds(joined)

    base_cols = ["sport", "layer", "feature_name", "layer_kind", "n", "coverage", "corr", "auc"]
    roi_cols = ["roi", "roi_bets"]
    out_cols = base_cols + roi_cols if has_odds else base_cols

    if joined.empty:
        return pd.DataFrame(columns=out_cols)

    rows = []
    for (layer, feature_name), group in joined.groupby(["layer", "feature_name"], sort=False):
        n = int(len(group))
        coverage = float(group["value"].notna().mean())

        usable = group.dropna(subset=["value", "y"])
        corr = np.nan
        auc = np.nan
        if len(usable) >= 2 and usable["value"].nunique() > 1 and usable["y"].nunique() > 1:
            corr = float(usable["value"].corr(usable["y"]))
        if len(usable) >= 2 and usable["y"].nunique() == 2:
            try:
                auc = float(roc_auc_score(usable["y"], usable["value"]))
            except ValueError:
                auc = np.nan

        row = {
            "sport": sport,
            "layer": layer,
            "feature_name": feature_name,
            "layer_kind": _layer_kind(layer),
            "n": n,
            "coverage": round(coverage, 5),
            "corr": round(corr, 5) if corr == corr else np.nan,
            "auc": round(auc, 5) if auc == auc else np.nan,
        }

        if has_odds:
            odds_usable = group.dropna(subset=[*_ODDS_COLS, "y"])
            # de-dup: a (event, entity) result row is repeated once per
            # feature already selected by the groupby, so it is already a
            # single bet-per-row slice here.
            if len(odds_usable):
                roi_result = flat_stake_roi(
                    odds_usable["model_p"].to_numpy(),
                    odds_usable["market_p"].to_numpy(),
                    odds_usable["decimal_odds"].to_numpy(),
                    odds_usable["y"].to_numpy(),
                    edge_threshold=edge_threshold,
                )
                row["roi"] = roi_result["roi"]
                row["roi_bets"] = roi_result["bets"]
            else:
                row["roi"] = None
                row["roi_bets"] = 0

        rows.append(row)

    out = pd.DataFrame(rows, columns=out_cols)
    return out.sort_values(["layer_kind", "layer", "feature_name"]).reset_index(drop=True)


def layer_summary(graded_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate `grade_layers()` output up to one row per (sport, layer),
    still split by `layer_kind`: mean corr, mean |corr|, mean coverage, mean
    AUC, and how many features fed the layer. Sorted by predictiveness
    (mean |corr|, descending) within the environment/scoring split.
    """
    cols = ["sport", "layer", "layer_kind", "mean_corr", "mean_abs_corr",
            "mean_coverage", "mean_auc", "n_features"]
    if graded_df.empty:
        return pd.DataFrame(columns=cols)

    g = graded_df.copy()
    g["abs_corr"] = g["corr"].abs()

    summary = (
        g.groupby(["sport", "layer", "layer_kind"], sort=False)
         .agg(
             mean_corr=("corr", "mean"),
             mean_abs_corr=("abs_corr", "mean"),
             mean_coverage=("coverage", "mean"),
             mean_auc=("auc", "mean"),
             n_features=("feature_name", "nunique"),
         )
         .reset_index()
    )
    for col in ("mean_corr", "mean_abs_corr", "mean_coverage", "mean_auc"):
        summary[col] = summary[col].round(5)

    summary = summary.sort_values(
        ["layer_kind", "mean_abs_corr"], ascending=[True, False]
    ).reset_index(drop=True)
    return summary[cols]


def layer_roi(
    layer_df: pd.DataFrame,
    result_df: pd.DataFrame,
    sport: str,
    *,
    layer: str | None = None,
    edge_threshold: float = 0.02,
) -> dict | None:
    """
    Flat-stake ROI for `sport` (optionally restricted to a single `layer`),
    reusing `models.evaluate.flat_stake_roi` verbatim for the accounting.

    Requires model_p, market_p, and decimal_odds on `result_df` — the priced
    columns a real backtest needs before any tiering claim is meaningful.
    When those columns are absent this is a no-op: returns None rather than
    fabricating a price. 'void' outcomes are excluded, as are duplicate
    (event_id, entity_id) bets produced by the layer_score join (one bet per
    settled result, not one per feature row).
    """
    joined = _join(layer_df, result_df, sport)
    if not _has_odds(joined):
        logger.info("layer_roi: odds columns absent for sport=%s — skipping ROI", sport)
        return None

    if layer is not None:
        joined = joined.loc[joined["layer"] == layer]

    usable = joined.dropna(subset=[*_ODDS_COLS, "y"])
    usable = usable.drop_duplicates(subset=["event_id", "entity_id"])

    if usable.empty:
        return {"edge_threshold": edge_threshold, "bets": 0, "roi": None,
                "profit": 0.0, "hit_rate": None}

    return flat_stake_roi(
        usable["model_p"].to_numpy(),
        usable["market_p"].to_numpy(),
        usable["decimal_odds"].to_numpy(),
        usable["y"].to_numpy(),
        edge_threshold=edge_threshold,
    )
