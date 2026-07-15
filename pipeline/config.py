"""
pipeline/config.py
==================
Central configuration for all paths, constants, and environment variables.
Every module imports from here — never hardcode paths elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    # python-dotenv only loads a local .env for dev convenience; in CI/prod the
    # runner supplies env vars directly. A missing dev dep must never crash the
    # whole pipeline at import time.
    pass

# ── Project root ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Data layer paths ────────────────────────────────────────────────────────────
DATA_DIR    = PROJECT_ROOT / "data"
BRONZE_DIR  = DATA_DIR / "bronze"
SILVER_DIR  = DATA_DIR / "silver"
GOLD_DIR    = DATA_DIR / "gold"
DUCKDB_PATH = SILVER_DIR / "baseballiq.duckdb"

# ── Model artifacts ─────────────────────────────────────────────────────────────
MODELS_DIR    = PROJECT_ROOT / "models" / "artifacts"
MODEL_PATH    = MODELS_DIR / "pitcher_effectiveness_v1.pkl"

# ── Reports output ──────────────────────────────────────────────────────────────
REPORTS_DIR = PROJECT_ROOT / "reports" / "output"

# ── API keys ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Demo mode (set DEMO_MODE=1 to skip live API calls) ──────────────────────────
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"

# ── Storage backend selector (see pipeline/storage.py) ──────────────────────────
#   'local'    → LocalParquetBackend (default; offline, tests + demo)
#   'supabase' → SupabaseBackend (publish Gold to Supabase Postgres)
VOSS_STORAGE = os.getenv("VOSS_STORAGE", "local")

# League-average baselines moved to pipeline.sports.SportConfig.league_avg — they
# are per-sport, not global. Import via `pipeline.sports.get_sport(sport).league_avg`.


def gold_export_path(sport_key: str, filename: str) -> Path:
    """Sport-scoped Gold parquet path: data/gold/<sport>/<filename>.

    Keeps each sport's Gold artifacts isolated so a new sport needs no path
    surgery. MLB's historical flat path (data/gold/<file>) is still readable by
    callers that pass their own explicit path.
    """
    return GOLD_DIR / sport_key / filename


# ── Ensure directories exist on import ──────────────────────────────────────────
for _dir in [BRONZE_DIR, SILVER_DIR, GOLD_DIR, MODELS_DIR, REPORTS_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)
