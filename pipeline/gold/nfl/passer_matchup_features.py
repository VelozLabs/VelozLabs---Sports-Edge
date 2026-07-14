"""
pipeline/gold/nfl/passer_matchup_features.py
=============================================
NFL wide Gold table `passer_matchup_features` (STUB).

Grain: one row = one passer (QB) in one game. Target: `pass_td_scored`
(see pipeline.sports.get_sport('nfl').target). Mirrors the MLB matchup-table
assembly (pipeline/gold/matchup_table.py): form block + opponent block +
context block → one row, adding NO windows of its own, with all rolling inputs
obeying `RANGE BETWEEN INTERVAL N DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING`.

Football-specific layer emphasis (do NOT copy MLB blindly):
    form      — passer rolling volume/efficiency (attempts, TD rate, aDOT)
    opponent  — defense pass-TD allowed rate, pressure/coverage proxies
    context   — GAME SCRIPT: spread / total / implied team total (blowouts kill
                passing props); weather only for outdoor/wind games; is_home
    actives   — confirmed-active gate from `lineup_confirmed` (espn inactives)

Every feature registered in the NFL feature module (the per-sport analog of
models/features.py REGISTRY) with an availability tag; line-move + inactives are
the same-day edge, not park/weather. `validate_features` still governs what may
enter the model, and `pipeline/publish.py:build_layer_score` still projects the
tall `layer_score` rows for cross-sport backtesting — unchanged.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

BUILD_TABLE = "passer_matchup_features"   # DuckDB build table (SportConfig.build_table)
TARGET = "pass_td_scored"


def build_passer_matchup_features(con, export_path=None) -> None:
    """Assemble the wide NFL Gold table (STUB).

    Same signature contract as
    pipeline.gold.matchup_table.build_batter_matchup_features so the orchestrator
    and the storage-seam export path are reused verbatim. Implementation lands
    with the NFL feature blocks; until then this raises.
    """
    raise NotImplementedError(
        "NFL Gold `passer_matchup_features` is stubbed. Build form/opponent/"
        "context blocks (line-move + inactives first), then assemble one row per "
        "passer×game with target `pass_td_scored`, reusing the INTERVAL 1 DAY "
        "PRECEDING window convention."
    )
