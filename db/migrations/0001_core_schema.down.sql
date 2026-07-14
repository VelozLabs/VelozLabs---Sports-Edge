-- ============================================================================
-- Voss Edge — sport-agnostic core schema (migration 0001, DOWN)
-- ============================================================================
-- Reverses 0001_core_schema.up.sql. Drop in reverse dependency order; CASCADE
-- clears the per-table triggers/indexes. The shared trigger function is dropped
-- last. Idempotent via IF EXISTS.
-- ============================================================================

DROP TABLE IF EXISTS layer_score       CASCADE;
DROP TABLE IF EXISTS lineup_confirmed  CASCADE;
DROP TABLE IF EXISTS result            CASCADE;
DROP TABLE IF EXISTS bet               CASCADE;
DROP TABLE IF EXISTS prop_price        CASCADE;
DROP TABLE IF EXISTS prop              CASCADE;
DROP TABLE IF EXISTS entity            CASCADE;
DROP TABLE IF EXISTS event             CASCADE;
DROP TABLE IF EXISTS source            CASCADE;
DROP TABLE IF EXISTS sport             CASCADE;

DROP FUNCTION IF EXISTS set_updated_at() CASCADE;
