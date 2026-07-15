-- ============================================================================
-- Voss Edge — MLB wide serving table `mlb_matchup_features` (migration 0002, UP)
-- ============================================================================
-- The per-sport WIDE ML-grain table, materialized OUTSIDE the sport-agnostic
-- core (its columns are MLB-specific). Mirrors the DuckDB build table
-- `batter_matchup_features` (pipeline/gold/matchup_table.py): one row per
-- batter × game. publish.publish_matchup_wide upserts on (batter_id, game_pk).
--
-- Grain (batter_id, game_pk) == core (sport='mlb', entity_id, event_id). The
-- authoritative FKs live on the tall `layer_score`; this table stays FK-free so
-- the wide publish never blocks on parent-row timing. RLS enabled, no policies
-- (service-role-only), consistent with the core schema.
-- ============================================================================

CREATE TABLE mlb_matchup_features (
    -- keys
    batter_id                   bigint NOT NULL,
    game_pk                     bigint NOT NULL,
    game_date                   date   NOT NULL,
    batter_team                 text,
    stand                       text,
    opp_starter_id              bigint,
    -- target
    hr_hit                      integer,
    -- same-game outcomes (quarantined non-features)
    pa_this_game                integer,
    lineup_slot_actual          integer,
    -- batter form (T-1day)
    b_pa_30d                    double precision,
    b_hr_per_pa_7d              double precision,
    b_hr_per_pa_30d             double precision,
    b_hr_per_pa_365d            double precision,
    b_hr_per_pa_shrunk          double precision,
    b_barrel_rate_30d           double precision,
    b_hard_hit_30d              double precision,
    b_fb_rate_30d               double precision,
    b_pull_fb_rate_30d          double precision,
    b_avg_ev_30d                double precision,
    b_max_ev_30d                double precision,
    b_avg_pa_per_game_30d       double precision,
    b_avg_slot_30d              double precision,
    b_hr_per_pa_vs_hand_365d    double precision,
    -- opposing starter
    opp_starter_throws          text,
    same_handed                 integer,
    p_pa_30d                    double precision,
    p_hr_per_pa_allowed_30d     double precision,
    p_hr_per_pa_allowed_365d    double precision,
    p_barrel_rate_allowed_30d   double precision,
    p_fb_rate_allowed_30d       double precision,
    p_avg_ev_allowed_30d        double precision,
    -- opposing bullpen
    bp_hr_per_pa_allowed_30d    double precision,
    -- game context
    is_home                     integer,
    park_hr_factor_hand         double precision,
    park_hr_factor              double precision,
    roof                        text,
    is_night                    integer,
    temp_f                      double precision,
    wind_out_mph                double precision,
    -- audit
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    source_system               text NOT NULL DEFAULT 'duckdb_gold_publish',
    CONSTRAINT uq_mlb_matchup_features UNIQUE (batter_id, game_pk)
);
CREATE INDEX idx_mlb_matchup_slate ON mlb_matchup_features (game_date);
CREATE TRIGGER trg_mlb_matchup_touch BEFORE UPDATE ON mlb_matchup_features
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE mlb_matchup_features ENABLE ROW LEVEL SECURITY;
