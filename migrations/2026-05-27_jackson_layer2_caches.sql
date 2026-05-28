-- Jackson Layer-3 permits cache.
--
-- Layer-2 (assessment, records) is a SQL pass-through over parcels_jackson L1
-- inline data (Marion pattern) — no L2 cache table needed; L1 IS the cache,
-- refreshed by the daily delta job.
--
-- Layer-3 (permits) IS cached because each call hits the live GIS Permit
-- FeatureServer (spatial.jacksoncountyor.gov DevServ/Permit) with a spatial
-- filter over the parcel polygon. The Jackson service exposes 243k building
-- permits + 40k land-use permits as point geometry with pre-built ACA-Oregon
-- Accela deep links — way richer than the deep-link-only fetch_county_permits
-- fallback we use for Crook/Marion. So fetch_jackson_permits returns real
-- structured permit data; this table caches it 24h to be polite to the county
-- server (and to keep Sophia responses fast).

BEGIN;

CREATE TABLE IF NOT EXISTS jackson_permits_cache (
    taxlot          TEXT PRIMARY KEY,
    permits_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
    permit_count    INTEGER NOT NULL DEFAULT 0,
    bldg_count      INTEGER NOT NULL DEFAULT 0,
    landuse_count   INTEGER NOT NULL DEFAULT 0,
    query_bbox      TEXT,            -- bbox used for the spatial query, for diagnostics
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_jackson_permits_cache_fetched_at
    ON jackson_permits_cache (fetched_at);

COMMIT;
