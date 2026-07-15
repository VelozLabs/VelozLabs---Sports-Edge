# ⚾ BaseballIQ — System Architecture

---

## Project Overview

**BaseballIQ** is an MLB analytics platform built on Statcast data. It ingests raw pitch-by-pitch event data, processes it through a medallion architecture, enriches it with LLM-generated insights via Google Gemini, trains a predictive model for pitcher effectiveness, and surfaces everything through a static betting dashboard and AI-powered scouting report system.

**Why MLB Statcast?**
- Richest free public sports dataset available (millions of events/season)
- Dense numeric features: exit velocity, launch angle, spin rate, pitch movement
- Well-suited for ML: strong signal, high volume, clean labels
- `pybaseball` makes ingestion trivial

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     RAW DATA SOURCES                            │
│  pybaseball / MLB Statcast API                                  │
└───────────────────┬─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│              BRONZE LAYER  (raw/immutable)                      │
│   Parquet files partitioned by  game_date                       │
│   Schema: raw Statcast columns, no transformation               │
└───────────────────┬─────────────────────────────────────────────┘
                    │  cleaning + typing + deduplication
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│              SILVER LAYER  (cleaned + normalized)               │
│   DuckDB tables: pitches, at_bats, games, players               │
│   Feature engineering: rolling averages, zone maps, batted ball │
└───────────────────┬─────────────────────────────────────────────┘
                    │  aggregation + business logic
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│              GOLD LAYER  (analytical datasets)                  │
│   pitcher_game_summary  │  batter_game_summary  │  players      │
│   Ready for ML, dashboards, and LLM enrichment                  │
└──────────────┬──────────────────────────┬───────────────────────┘
               │                          │
               ▼                          ▼
┌──────────────────────┐    ┌─────────────────────────────────────┐
│   ML MODEL LAYER     │    │       LLM ENRICHMENT LAYER          │
│  XGBoost: pitcher    │    │  Gemini API → summaries, anomalies, │
│  effectiveness model │    │  narrative insights per game/player  │
│  + SHAP values       │    │  Output: JSON insight blobs stored  │
│                      │    │  back into Gold layer               │
└──────────┬───────────┘    └──────────────┬──────────────────────┘
           │                               │
           └──────────────┬────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│              SCOUTING REPORT ENGINE                             │
│   Merges Gold stats + ML scores + LLM text                      │
│   Renders text scouting report per player/game                  │
└───────────────────┬─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│              STATIC BETTING DASHBOARD                           │
│   Dark-themed HTML with DataTables (CSW%, Whiff%, Velo Delta)  │
│   AI insight tooltips │ Daily auto-refresh via GitHub Actions   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Folder Structure

```
baseballiq-test/
│
├── README.md
├── ARCHITECTURE.md
├── UPDATES.md                  # Change log
├── pyproject.toml              # uv / pip project config
├── requirements.txt            # Flat dependency list
├── .env.example                # API keys template
├── Makefile                    # make ingest, make clean, make dashboard
│
├── data/
│   └── gold/                   # Analytical outputs (committed for demo)
│       ├── pitcher_game_summary.parquet
│       ├── batter_game_summary.parquet
│       ├── players.parquet
│       └── llm_insights.json
│
├── pipeline/
│   ├── config.py               # Paths, constants, env vars
│   ├── orchestrator.py         # Runs full pipeline end-to-end
│   ├── ingestion/
│   │   └── statcast_ingestion.py
│   ├── silver/
│   │   ├── cleaning.py
│   │   └── feature_engineering.py
│   └── gold/
│       └── aggregations.py
│
├── enrichment/
│   └── llm_enrichment.py       # Gemini API client + prompt templates + insight writer
│
├── models/
│   ├── train.py                # XGBoost training + SHAP + time-series CV
│   ├── predict.py              # Inference wrapper
│   └── artifacts/              # Saved model files
│
├── reports/
│   └── scouting_report.py      # Report assembly + text renderer
│
├── dashboard/
│   ├── app.py                  # Streamlit entry point (legacy, still functional)
│   └── templates/
│       └── index.html          # Jinja2 template for static betting dashboard
│
├── build_site.py               # Static site generator (DuckDB → HTML)
├── generate_demo_data.py       # Synthetic Statcast data generator
│
├── tests/
│   └── test_pipeline.py
│
├── docs/                       # Generated static site output (GitHub Pages)
│
├── .github/
│   └── workflows/
│       └── daily_pipeline.yml  # Automated daily ingestion + refresh
│
└── .streamlit/                 # Streamlit configuration
```

---

## Schema Design

### Silver Layer — DuckDB Tables

**`pitches`** (core fact table)
```sql
CREATE TABLE pitches (
    pitch_id        VARCHAR PRIMARY KEY,
    game_pk         INTEGER,
    game_date       DATE,
    pitcher_id      INTEGER,
    batter_id       INTEGER,
    inning          INTEGER,
    pitch_type      VARCHAR,        -- FF, SL, CH, CU, SI...
    release_speed   FLOAT,          -- mph
    release_spin    FLOAT,          -- rpm
    pfx_x           FLOAT,          -- horizontal break (inches)
    pfx_z           FLOAT,          -- vertical break (inches)
    plate_x         FLOAT,          -- location at plate
    plate_z         FLOAT,
    zone            INTEGER,        -- 1-14 Statcast zone
    description     VARCHAR,        -- called_strike, swinging_strike...
    events          VARCHAR,        -- strikeout, home_run, single...
    launch_speed    FLOAT,          -- exit velocity
    launch_angle    FLOAT,
    estimated_ba    FLOAT,          -- xBA
    estimated_woba  FLOAT,          -- xwOBA
    delta_run_exp   FLOAT,          -- run expectancy change
    is_barrel       BOOLEAN,
    created_at      TIMESTAMP DEFAULT NOW()
);
```

**`pitcher_game_summary`** (Gold aggregation)
```sql
CREATE TABLE pitcher_game_summary AS
SELECT
    pitcher_id,
    game_date,
    game_pk,
    COUNT(*)                                    AS total_pitches,
    AVG(release_speed)                          AS avg_velo,
    AVG(release_spin)                           AS avg_spin,
    -- Whiff rate
    SUM(CASE WHEN description = 'swinging_strike' THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN description LIKE '%swing%' THEN 1 ELSE 0 END), 0)
                                                AS whiff_rate,
    -- CSW rate (called strike + whiff)
    SUM(CASE WHEN description IN ('called_strike','swinging_strike') THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0)                   AS csw_rate,
    AVG(estimated_woba)                         AS avg_xwoba_allowed,
    SUM(delta_run_exp)                          AS total_re_delta,
    -- Rolling 30-day context added in feature_engineering.py
    NULL::FLOAT                                 AS rolling_30d_whiff_rate,
    NULL::FLOAT                                 AS rolling_30d_avg_velo
FROM pitches
WHERE pitcher_id IS NOT NULL
GROUP BY 1,2,3;
```

---

## Feature Engineering (Silver → Gold)

Key engineered features per pitcher-game row:

| Feature | Logic |
|---|---|
| `velo_vs_30d_avg` | `avg_velo - rolling_30d_avg_velo` |
| `whiff_rate_delta` | `whiff_rate - rolling_30d_whiff_rate` |
| `stuff_diversity` | entropy of pitch_type distribution |
| `zone_rate` | pitches in zones 1-9 / total |
| `chase_rate` | swings on balls / balls |
| `barrel_rate_allowed` | barrels / batted balls |
| `rolling_30d_csw_rate` | 30-day rolling CSW average |
| `avg_h_break`, `avg_v_break` | pitch movement metrics |

---

## LLM Enrichment Layer

After Gold tables are built, the enrichment module:
1. Pulls pitcher rows for a given game date
2. Constructs a structured prompt with stats
3. Calls Gemini 2.0 Flash API (free tier)
4. Parses structured JSON response (using `response_mime_type`)
5. Stores the result in `llm_insights` DuckDB table and exports to Gold

### Prompt Templates (in `enrichment/llm_enrichment.py`)

**Pitcher Insight Prompt:**
```
You are a baseball analyst writing for an internal scouting system.

Given pitcher performance data for [PITCHER_NAME] on [DATE]:

Pitches thrown: {total_pitches}
Avg velocity: {avg_velo} mph (season avg: {season_avg_velo} mph)
Whiff rate: {whiff_rate:.1%} (league avg: 25.4%)
CSW rate: {csw_rate:.1%}
xwOBA allowed: {avg_xwoba_allowed:.3f}

Respond in JSON with keys:
- "performance_tier": one of ["elite", "above_avg", "average", "below_avg", "poor"]
- "headline": one sentence summary (max 15 words)
- "key_finding": 2-3 sentences on the most important statistical story
- "concern_flag": null OR one sentence on a red flag if present
- "pitch_mix_note": null OR one sentence on pitch mix trends
```

---

## ML Model: Pitcher Effectiveness Prediction

**Goal:** Predict `csw_rate` (called strikes + whiffs / total pitches) for a pitcher's next game, given recent performance trends.

**Features (from Gold layer):**
```python
FEATURE_COLS = [
    "rolling_30d_csw_rate",
    "rolling_30d_whiff_rate",
    "velo_vs_30d_avg",
    "whiff_rate_delta",
    "stuff_diversity",
    "zone_rate",
    "chase_rate",
    "barrel_rate_allowed",
    "avg_xwoba_allowed",
    "avg_spin",
    "avg_h_break",
    "avg_v_break",
    "total_pitches",
    "home_away",
]
TARGET = "csw_rate"
```

**Model:** XGBoost Regressor
- Handles missing values natively, fast inference, SHAP-compatible
- Evaluation: 5-fold time-series cross-validation (no data leakage across dates)
- Metrics: RMSE, MAE, R²

**SHAP explainability** — every prediction includes feature attributions so the scouting report can say *why* a pitcher is projected to dominate.

---

## Scouting Report Engine

**Assembly logic** (`reports/scouting_report.py`):
1. Query Gold: get pitcher's last N games stats
2. Run `predict.py` → get predicted CSW and SHAP values
3. Pull LLM insight blob from `llm_insights`
4. Render text-based scouting report
5. Return JSON for dashboard consumption

---

## Dashboard

The project includes two dashboard options:

| Dashboard | Technology | Use Case |
|---|---|---|
| **Static Betting Dashboard** | Jinja2 → HTML + DataTables | Fast, dark-themed, daily stat checking |
| **Streamlit Dashboard** (legacy) | Streamlit + Plotly | Interactive exploration, live AI report generation |

**Static Dashboard Stack:** Jinja2 + DataTables + Plotly.js + GitHub Pages (free hosting)
**Streamlit Stack:** Streamlit + Plotly + DuckDB + Gemini API (on-demand generation)

---

## Automation

The platform runs autonomously via GitHub Actions:

- **Schedule:** Daily at 6 AM ET (during baseball season)
- **Pipeline:** Ingest → Clean → Gold → Gemini Enrich → Build Static Site → Git Push
- **Manual trigger:** Available via workflow_dispatch with optional date override
- **Cost:** $0 (GitHub free tier + Gemini free tier)

---

## Engineering Practices

### Reproducibility
- `pyproject.toml` with pinned dependencies
- `Makefile` targets: `make ingest DATE_START=...`, `make train`, `make report PLAYER_ID=605483`
- `.env.example` for all secrets

### Testing
- `pytest` with fixtures loading sample Parquet files
- Tests for: schema validation, feature shape, data integrity

### Quickstart

```bash
git clone https://github.com/VelozLabs/baseballiq-test.git
cd baseballiq-test
pip install -r requirements.txt

# Set environment variables
cp .env.example .env
# Add GEMINI_API_KEY to .env (free at https://aistudio.google.com/apikey)

# Run full pipeline for one week of games
make ingest DATE_START=2024-07-01 DATE_END=2024-07-07
make clean
make gold
make enrich
make train

# Launch dashboard
make dashboard
```
