-- Lane Layer 2 fetcher cache tables.
--
-- Two tables, two access patterns:
--
--   lane_assessment_cache: 1:1 with a Lane taxlot. Holds the rendered fetch_lane_assessment
--     payload so the Sophia tool returns instantly on cache hit. Underlying data is
--     parcels_lane Layer 1 inline (Lane is the richest L1 in TERRA — owner, RMV, AV, tax
--     certified, zoning, jurisdiction all native), so cache is convenience + future-proofing
--     for the moment Lane A&T scrape lands and adds tax history.
--
--   lane_records_cache: 1:1 with a Lane taxlot. Lane County Clerk's official-records
--     surface has no public queryable API (mark IN FLIGHT in fetch_lane_records). This
--     table is structured exactly like multco_records_cache so when the upstream finally
--     gets a path — paid Tyler tier, in-person export, or future RLID public endpoint —
--     it drops in without a schema change.
--
-- Both tables follow the DIAL cache pattern: jsonb payload, fetched_at timestamp,
-- last_error column for cached failure state, ON CONFLICT upsert via the unique key.
--
-- L3 permits cache for Lane (when state ePermitting branch is hit) shares the existing
-- oregon_permits_cache table keyed by (taxlot, county_fips) — no new permits table needed
-- because Lane participates in the state Accela portal.

BEGIN;

CREATE TABLE IF NOT EXISTS lane_assessment_cache (
    taxlot          TEXT PRIMARY KEY,
    account_number  INTEGER,
    payload_json    JSONB,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_lane_assessment_cache_account
    ON lane_assessment_cache (account_number);
CREATE INDEX IF NOT EXISTS idx_lane_assessment_cache_fetched_at
    ON lane_assessment_cache (fetched_at);


CREATE TABLE IF NOT EXISTS lane_records_cache (
    taxlot          TEXT PRIMARY KEY,
    owner_name      TEXT,
    documents_json  JSONB,
    document_count  INTEGER,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_lane_records_cache_owner
    ON lane_records_cache (owner_name);
CREATE INDEX IF NOT EXISTS idx_lane_records_cache_fetched_at
    ON lane_records_cache (fetched_at);

COMMIT;
