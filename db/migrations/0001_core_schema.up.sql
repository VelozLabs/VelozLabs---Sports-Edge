-- ============================================================================
-- Voss Edge — sport-agnostic core schema (migration 0001, UP)
-- ============================================================================
-- Target: Supabase Postgres (system-of-record). Heavy transforms stay in
-- DuckDB on GitHub Actions; only Gold + serving tables live here.
--
-- Conventions on every fact table:
--   * sport         → partition key of the whole system (composite PKs, because
--                     game_pk / batter_id are unique only WITHIN a sport)
--   * source_root   → data provenance (the medallion origin; 'statcast', …)
--   * source_system → the writer (audit)
--   * created_at / updated_at timestamptz DEFAULT now()  (trigger maintains updated_at)
--
-- Index note: this migration creates indexes with plain CREATE INDEX because the
-- tables are EMPTY at creation (no lock concern). Index additions on POPULATED
-- tables later must use CREATE INDEX CONCURRENTLY (outside a txn). Supabase MCP
-- apply_migration wraps this file in a transaction, which is correct here.
-- ============================================================================

-- ── shared updated_at trigger ───────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── sport: registry root ─────────────────────────────────────────────────────
CREATE TABLE sport (
    sport_key      text PRIMARY KEY,                 -- 'mlb','nfl','cfb'
    display_name   text NOT NULL,
    entity_noun    text NOT NULL,                     -- 'batter','passer'
    event_noun     text NOT NULL DEFAULT 'game',
    active         boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    source_system  text NOT NULL DEFAULT 'voss_edge_pipeline'
);
CREATE TRIGGER trg_sport_touch BEFORE UPDATE ON sport
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── source: provenance registry ──────────────────────────────────────────────
CREATE TABLE source (
    source_root     text PRIMARY KEY,                 -- 'statcast','mlb_statsapi',…
    sport           text REFERENCES sport(sport_key), -- NULL = cross-sport source
    layer           text NOT NULL CHECK (layer IN ('bronze','silver','gold','market')),
    is_night_before boolean NOT NULL DEFAULT false,   -- drives recorded_at = game_date - 1
    description     text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    source_system   text NOT NULL DEFAULT 'voss_edge_pipeline'
);
CREATE INDEX idx_source_sport ON source (sport);
CREATE TRIGGER trg_source_touch BEFORE UPDATE ON source
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── event: generic game (MLB game_pk → event_id) ─────────────────────────────
CREATE TABLE event (
    sport          text NOT NULL REFERENCES sport(sport_key),
    event_id       bigint NOT NULL,
    game_date      date NOT NULL,
    commence_ts    timestamptz,
    home_team      text,
    away_team      text,
    venue_name     text,
    status         text,                              -- 'Scheduled','Final','Postponed'
    source_root    text NOT NULL REFERENCES source(source_root),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    source_system  text NOT NULL DEFAULT 'voss_edge_pipeline',
    PRIMARY KEY (sport, event_id)
);
CREATE INDEX idx_event_sport_date  ON event (sport, game_date);
CREATE INDEX idx_event_source_root ON event (source_root);
CREATE TRIGGER trg_event_touch BEFORE UPDATE ON event
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── entity: generic player (MLB batter_id/pitcher_id → entity_id) ─────────────
CREATE TABLE entity (
    sport          text NOT NULL REFERENCES sport(sport_key),
    entity_id      bigint NOT NULL,
    full_name      text,
    entity_type    text NOT NULL,                     -- 'batter','pitcher','qb',…
    bats           text,                              -- stand: 'L'/'R'/'S'
    throws         text,                              -- p_throws
    source_root    text NOT NULL REFERENCES source(source_root),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    source_system  text NOT NULL DEFAULT 'voss_edge_pipeline',
    PRIMARY KEY (sport, entity_id)
);
-- expression index for the sportsbook name join (betting/edge.normalize_name)
CREATE INDEX idx_entity_name_norm  ON entity (sport, lower(full_name));
CREATE INDEX idx_entity_source_root ON entity (source_root);
CREATE TRIGGER trg_entity_touch BEFORE UPDATE ON entity
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── prop: a prop line definition (event × entity × market × line) ────────────
CREATE TABLE prop (
    prop_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sport          text NOT NULL REFERENCES sport(sport_key),
    event_id       bigint NOT NULL,
    entity_id      bigint NOT NULL,
    market         text NOT NULL,                     -- 'batter_home_runs','player_pass_tds'
    line           numeric(6,2) NOT NULL,             -- 0.5 for HR yes/no
    slate_date     date NOT NULL,
    source_root    text NOT NULL REFERENCES source(source_root),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    source_system  text NOT NULL DEFAULT 'voss_edge_pipeline',
    FOREIGN KEY (sport, event_id)  REFERENCES event  (sport, event_id),
    FOREIGN KEY (sport, entity_id) REFERENCES entity (sport, entity_id),
    CONSTRAINT uq_prop UNIQUE (sport, event_id, entity_id, market, line)
);
CREATE INDEX idx_prop_sport_event  ON prop (sport, event_id);
CREATE INDEX idx_prop_sport_entity ON prop (sport, entity_id);
CREATE INDEX idx_prop_slate        ON prop (sport, slate_date, market);
CREATE TRIGGER trg_prop_touch BEFORE UPDATE ON prop
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── prop_price: raw two-sided quote snapshots (APPEND-ONLY) ───────────────────
CREATE TABLE prop_price (
    prop_price_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sport          text NOT NULL REFERENCES sport(sport_key),
    prop_id        bigint NOT NULL REFERENCES prop(prop_id),
    book           text NOT NULL,
    side           text NOT NULL CHECK (side IN ('Yes','No','Over','Under')),
    american       integer NOT NULL,
    snapshot_ts    timestamptz NOT NULL,              -- exact collection instant
    slate_date     date NOT NULL,
    source_root    text NOT NULL REFERENCES source(source_root),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    source_system  text NOT NULL DEFAULT 'the_odds_api',
    CONSTRAINT uq_prop_price UNIQUE (prop_id, book, side, snapshot_ts)
);
CREATE INDEX idx_prop_price_prop      ON prop_price (prop_id);
CREATE INDEX idx_prop_price_slate     ON prop_price (sport, slate_date);
CREATE INDEX idx_prop_price_book_snap ON prop_price (book, snapshot_ts);
CREATE TRIGGER trg_prop_price_touch BEFORE UPDATE ON prop_price
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── bet: priced candidate/placed pick (EDGE_COLUMNS + TierAssignment) ─────────
CREATE TABLE bet (
    bet_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sport             text NOT NULL REFERENCES sport(sport_key),
    prop_id           bigint NOT NULL REFERENCES prop(prop_id),
    slate_date        date NOT NULL,
    snapshot_ts       timestamptz NOT NULL,
    book              text NOT NULL,
    line_american     integer NOT NULL,
    decimal_odds      numeric(8,4) NOT NULL,
    implied_p         numeric(8,5) NOT NULL,
    model_p           numeric(8,5) NOT NULL,          -- calibrated probability only
    market_p_devig    numeric(8,5) NOT NULL,
    devig_quality     text NOT NULL CHECK (devig_quality IN ('two_way','assumed')),
    edge              numeric(8,5) NOT NULL,          -- model_p - market_p_devig
    ev_per_unit       numeric(8,5) NOT NULL,
    kelly_frac        numeric(8,5) NOT NULL,
    -- tier block (betting/tiers.TierAssignment)
    tier              text CHECK (tier IN ('L1','L2','L3','L4','L5')),
    uncapped_tier     text,
    caps_applied      text[],
    data_completeness numeric(5,4),
    correlated        boolean NOT NULL DEFAULT false,
    rubric_version    text,
    stake_units       numeric(8,4),                   -- NULL until actually placed
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    source_system     text NOT NULL DEFAULT 'voss_edge_pipeline',
    CONSTRAINT uq_bet UNIQUE (prop_id, book, snapshot_ts, model_p)
);
CREATE INDEX idx_bet_prop  ON bet (prop_id);
CREATE INDEX idx_bet_slate ON bet (sport, slate_date);
-- partial index: dashboard/report only ever read bettable tiers (L1 = tracked, not bet)
CREATE INDEX idx_bet_actionable ON bet (sport, slate_date, edge DESC)
    WHERE tier IN ('L3','L4','L5');
CREATE TRIGGER trg_bet_touch BEFORE UPDATE ON bet
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── result: settlement ────────────────────────────────────────────────────────
CREATE TABLE result (
    result_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sport          text NOT NULL REFERENCES sport(sport_key),
    event_id       bigint NOT NULL,
    entity_id      bigint NOT NULL,
    market         text NOT NULL,
    actual_value   numeric,                           -- e.g. HR count this game
    outcome        text CHECK (outcome IN ('hit','miss','void')),
    bet_id         bigint REFERENCES bet(bet_id),     -- NULL for pure-backtest settlement
    settled_ts     timestamptz,
    game_date      date NOT NULL,
    source_root    text NOT NULL REFERENCES source(source_root),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    source_system  text NOT NULL DEFAULT 'voss_edge_pipeline',
    FOREIGN KEY (sport, event_id)  REFERENCES event  (sport, event_id),
    FOREIGN KEY (sport, entity_id) REFERENCES entity (sport, entity_id),
    CONSTRAINT uq_result UNIQUE (sport, event_id, entity_id, market)
);
CREATE INDEX idx_result_bet          ON result (bet_id);
CREATE INDEX idx_result_sport_event  ON result (sport, event_id);
CREATE INDEX idx_result_sport_entity ON result (sport, entity_id);
CREATE TRIGGER trg_result_touch BEFORE UPDATE ON result
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── lineup_confirmed: LINEUP_RELEASE availability gate ────────────────────────
CREATE TABLE lineup_confirmed (
    sport          text NOT NULL REFERENCES sport(sport_key),
    event_id       bigint NOT NULL,
    entity_id      bigint NOT NULL,
    lineup_slot    integer,                           -- lineup_slot_actual
    status         text NOT NULL DEFAULT 'confirmed'
                     CHECK (status IN ('confirmed','scratched')),
    confirmed_ts   timestamptz NOT NULL,
    game_date      date NOT NULL,
    source_root    text NOT NULL REFERENCES source(source_root),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    source_system  text NOT NULL DEFAULT 'mlb_statsapi',
    PRIMARY KEY (sport, event_id, entity_id),
    FOREIGN KEY (sport, event_id)  REFERENCES event  (sport, event_id),
    FOREIGN KEY (sport, entity_id) REFERENCES entity (sport, entity_id)
);
CREATE INDEX idx_lineup_sport_event ON lineup_confirmed (sport, event_id);
CREATE TRIGGER trg_lineup_touch BEFORE UPDATE ON lineup_confirmed
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── layer_score: thin TALL cross-sport, layer-isolated backtest store ─────────
CREATE TABLE layer_score (
    layer_score_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sport          text NOT NULL REFERENCES sport(sport_key),
    event_id       bigint NOT NULL,
    entity_id      bigint NOT NULL,
    layer          text NOT NULL,                     -- 'form','opponent','context','park','bullpen'
    feature_name   text NOT NULL,                     -- rolled-up / curated cross-sport feature
    value          double precision,                  -- NULL preserved = missing
    game_date      date NOT NULL,                      -- the game being predicted
    recorded_at    timestamptz NOT NULL,               -- when the value became knowable (§ hygiene)
    source_root    text NOT NULL REFERENCES source(source_root),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    source_system  text NOT NULL DEFAULT 'duckdb_gold_publish',
    FOREIGN KEY (sport, event_id)  REFERENCES event  (sport, event_id),
    FOREIGN KEY (sport, entity_id) REFERENCES entity (sport, entity_id),
    CONSTRAINT uq_layer_score UNIQUE (sport, event_id, entity_id, layer, feature_name)
);
CREATE INDEX idx_layer_score_grain ON layer_score (sport, event_id, entity_id);
-- the north-star index: "hold layer X fixed, sweep every other layer" ACROSS sports
CREATE INDEX idx_layer_score_layer ON layer_score (sport, layer, game_date);
CREATE INDEX idx_layer_score_feat  ON layer_score (sport, feature_name, game_date);
CREATE TRIGGER trg_layer_score_touch BEFORE UPDATE ON layer_score
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── seed rows ────────────────────────────────────────────────────────────────
INSERT INTO sport (sport_key, display_name, entity_noun, event_noun) VALUES
    ('mlb', 'MLB', 'batter', 'game'),
    ('nfl', 'NFL', 'passer', 'game'),
    ('cfb', 'CFB', 'passer', 'game');

INSERT INTO source (source_root, sport, layer, is_night_before, description) VALUES
    ('statcast',            'mlb', 'bronze', false, 'pybaseball / Baseball Savant pitch-by-pitch'),
    ('mlb_statsapi',        'mlb', 'bronze', false, 'MLB StatsAPI schedule/venue/roof/probables/lineups'),
    ('open_meteo',          NULL,  'bronze', true,  'Open-Meteo forecast (+ ERA5 archive); night-before forecast'),
    ('nflverse',            'nfl', 'bronze', false, 'nflverse via nflreadpy: pbp/box/snaps/rosters/schedule'),
    ('espn',                NULL,  'bronze', false, 'ESPN hidden API: live inactives/actives, fallback scores'),
    ('collegefootballdata', 'cfb', 'bronze', false, 'CollegeFootballData (CFBD) pbp/rosters/venues/schedule'),
    ('duckdb_gold_publish', NULL,  'gold',   false, 'DuckDB Gold layer published to Supabase'),
    ('the_odds_api',        NULL,  'market', false, 'The Odds API player-prop lines');
