"""
pipeline/publish.py
====================
Orchestration seam that maps DuckDB Gold DataFrames onto the sport-agnostic
core Postgres tables (db/migrations/0001_core_schema.up.sql) through a
`StorageBackend` (pipeline/storage.py).

This module is pure DataFrame-in / publish-out: callers read Gold tables out
of DuckDB themselves and hand this module the resulting `pandas.DataFrame`;
nothing here opens a DuckDB connection. Each `publish_*` helper only knows
two things a caller could otherwise get wrong — the target table name and
the natural key (which MUST equal the table's UNIQUE constraint, see
`StorageBackend.publish_table`) — and delegates the actual UPSERT to the
injected backend.

The one non-trivial piece is `build_layer_score`, which melts a sport's WIDE
matchup table (e.g. `batter_matchup_features`, pipeline/gold/matchup_table.py)
into the TALL `layer_score` shape so backtests can hold one layer fixed and
sweep the others across every sport. It is a schema-contract enforcer, not a
data validator: any wide feature column that is not registered in
`models.features.REGISTRY` is treated as a pipeline bug (a new Gold column
shipped without a feature-registry entry) and raises `ValueError` rather than
being silently dropped or silently published.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # avoid importing heavy deps at module load
    import pandas as pd

from models.features import BANNED_AS_FEATURES, REGISTRY
from pipeline.sports import get_sport
from pipeline.storage import StorageBackend

logger = logging.getLogger(__name__)

# ── layer classification ────────────────────────────────────────────────────

#: Column-name prefix → layer_score.layer. Checked in order; first match wins.
LAYER_MAP: dict[str, str] = {
    "b_": "form",
    "p_": "opponent",
    "bp_": "bullpen",
    "park_": "park",
}

#: Exact (non-prefixed) column names that classify as 'context'.
_CONTEXT_COLS: frozenset[str] = frozenset({
    "is_home", "is_night", "temp_f", "wind_out_mph", "roof",
})


def layer_for(feature_name: str) -> str:
    """Classify a wide feature column into its `layer_score.layer` value.

    Prefix rules (checked longest-prefix-first so `bp_` never falls through
    to `b_`): `b_*` -> 'form', `p_*` -> 'opponent', `bp_*` -> 'bullpen',
    `park_*` -> 'park'. A fixed set of context columns (`is_home`, `is_night`,
    `temp_f`, `wind_out_mph`, `roof`) map to 'context'.

    Anything else defaults to 'context' rather than raising — new one-off
    context columns (e.g. a future `humidity_pct`) are common and harmless to
    file under 'context'; it is unregistered *feature-registry* membership
    (see `build_layer_score`) that is treated as the hard error, not layer
    assignment.
    """
    if feature_name in _CONTEXT_COLS:
        return "context"
    # bp_ must be checked before b_ (both prefixes would otherwise match "bp_...").
    for prefix in ("bp_", "b_", "p_", "park_"):
        if feature_name.startswith(prefix):
            return LAYER_MAP[prefix]
    return "context"


# ── thin publish helpers ────────────────────────────────────────────────────
# Each one's only job is picking the natural_key that matches the table's
# UNIQUE constraint (see db/migrations/0001_core_schema.up.sql) and handing
# off to storage.publish_table. No transformation happens here.

def publish_events(df: "pd.DataFrame", storage: StorageBackend, sport: str) -> int:
    """Publish to `event`; natural key = uq (sport, event_id)."""
    return storage.publish_table(df, "event", ["sport", "event_id"], sport)


def publish_entities(df: "pd.DataFrame", storage: StorageBackend, sport: str) -> int:
    """Publish to `entity`; natural key = uq (sport, entity_id)."""
    return storage.publish_table(df, "entity", ["sport", "entity_id"], sport)


def publish_matchup_wide(df: "pd.DataFrame", storage: StorageBackend, sport: str) -> int:
    """Publish the sport's wide Gold table to its own per-sport serving table.

    Table and key come entirely from `pipeline.sports.SportConfig` so a new
    sport never needs a code change here: table = `sport.wide_table`, key =
    [`sport.entity_grain`, `sport.event_grain`] (e.g. ['batter_id','game_pk']).
    """
    cfg = get_sport(sport)
    return storage.publish_table(
        df, cfg.wide_table, [cfg.entity_grain, cfg.event_grain], sport
    )


def publish_prop_prices(df: "pd.DataFrame", storage: StorageBackend, sport: str) -> int:
    """Publish to `prop_price`; natural key = uq_prop_price (prop_id, book, side, snapshot_ts)."""
    return storage.publish_table(
        df, "prop_price", ["prop_id", "book", "side", "snapshot_ts"], sport
    )


def publish_bets(df: "pd.DataFrame", storage: StorageBackend, sport: str) -> int:
    """Publish to `bet`; natural key = uq_bet (prop_id, book, snapshot_ts, model_p)."""
    return storage.publish_table(
        df, "bet", ["prop_id", "book", "snapshot_ts", "model_p"], sport
    )


def publish_layer_score(long_df: "pd.DataFrame", storage: StorageBackend, sport: str) -> int:
    """Publish an already-tall frame (see `build_layer_score`) to `layer_score`.

    Natural key = uq_layer_score (sport, event_id, entity_id, layer, feature_name).
    """
    return storage.publish_table(
        long_df,
        "layer_score",
        ["sport", "event_id", "entity_id", "layer", "feature_name"],
        sport,
    )


# ── the wide -> tall melt ────────────────────────────────────────────────────

#: Columns on a wide matchup table that are never feature candidates even
#: though they aren't in BANNED_AS_FEATURES (identity keys, target, audit).
_NON_FEATURE_COLS: frozenset[str] = frozenset({
    "game_date", "batter_team", "stand", "opp_starter_id", "opp_starter_throws",
    "created_at", "updated_at", "source_root", "source_system",
})


def build_layer_score(
    wide_df: "pd.DataFrame",
    sport: str,
    *,
    source_root: str,
    is_night_before: bool = False,
    entity_id_col: str | None = None,
    event_id_col: str | None = None,
) -> "pd.DataFrame":
    """Melt a sport's wide Gold matchup frame into the tall `layer_score` shape.

    Output columns: sport, event_id, entity_id, layer, feature_name, value
    (double, NaN preserved as NULL), game_date, recorded_at, source_root.

    SCHEMA CONTRACT: every wide-frame column that is a feature candidate
    (i.e. not a key column, not `sport.target`, not in
    `models.features.BANNED_AS_FEATURES`, not a known audit/identity column)
    MUST be registered in `models.features.REGISTRY`. An unregistered column
    is treated as a pipeline bug — a new Gold feature shipped without an
    availability tag — and raises `ValueError` naming every offending column,
    rather than silently dropping or silently publishing it.

    `recorded_at`: if `is_night_before` (the source's forecast landed the
    evening before, e.g. Open-Meteo weather) then `recorded_at` = game_date -
    1 day; otherwise `recorded_at` = game_date. Both as midnight timestamps.

    `entity_id`/`event_id` come from `entity_id_col`/`event_id_col` when
    given, else default to the sport's `entity_grain`/`event_grain`
    (pipeline.sports.SportConfig).
    """
    import pandas as pd  # local import: heavy dep, keep module import light

    cfg = get_sport(sport)
    entity_col = entity_id_col or cfg.entity_grain
    event_col = event_id_col or cfg.event_grain

    key_cols = {entity_col, event_col, "sport", "event_id", "entity_id"}
    excluded = key_cols | _NON_FEATURE_COLS | BANNED_AS_FEATURES | {cfg.target}

    candidate_cols = [c for c in wide_df.columns if c not in excluded]

    registered = {f.name for f in REGISTRY}
    unregistered = [c for c in candidate_cols if c not in registered]
    if unregistered:
        raise ValueError(
            "build_layer_score: unregistered feature column(s) in wide frame "
            f"(add to models/features.py REGISTRY or exclude explicitly): {unregistered}"
        )

    feature_cols = [c for c in candidate_cols if c in registered]

    if not feature_cols:
        logger.warning("build_layer_score: no registered feature columns found to melt")

    id_vars = [entity_col, event_col, "game_date"]
    melted = wide_df[id_vars + feature_cols].melt(
        id_vars=id_vars,
        value_vars=feature_cols,
        var_name="feature_name",
        value_name="value",
    )

    game_date = pd.to_datetime(melted["game_date"])
    recorded_at = game_date - timedelta(days=1) if is_night_before else game_date

    out = pd.DataFrame({
        "sport": sport,
        "event_id": melted[event_col],
        "entity_id": melted[entity_col],
        "layer": melted["feature_name"].map(layer_for),
        "feature_name": melted["feature_name"],
        "value": melted["value"].astype("float64"),  # NaN preserved as NULL on publish
        "game_date": melted["game_date"],
        "recorded_at": recorded_at,
        "source_root": source_root,
    })
    logger.info(
        "build_layer_score: %d wide rows x %d features -> %d long rows (sport=%s)",
        len(wide_df), len(feature_cols), len(out), sport,
    )
    return out
