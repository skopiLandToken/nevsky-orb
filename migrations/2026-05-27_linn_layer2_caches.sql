-- Linn Layer 2 fetcher cache tables.
--
-- Two tables, two access patterns:
--
--   linn_assessment_cache: 1:1 with a Linn taxlot. Holds the rendered
--     fetch_linn_assessment payload. Linn L1 is boundaries-only (ORMAP cartographic
--     polygons), so the assessment fetcher joins pub_sales (gis.co.linn.or.us/.../
--     pub_sales/MapServer/0) by PIN and surfaces the latest sale snapshot
--     (owner/buyer, siteaddr, sitecity, propcls, RMVcls, sqft, yearblt, beds/baths,
--     saleprice, saledate, deed Recording number). Multi-year valuation timeseries +
--     current-year RMV/AV stay IN FLIGHT (assessor portal not yet located — Linn
--     County's primary site is Cloudflare-gated, blocking automated portal discovery).
--
--   linn_records_cache: 1:1 with a Linn taxlot. pub_sales is the records surface
--     (every row is a deed Recording with seller/buyer/instrument). Caches the
--     resolved deed chain by taxlot so the records fetcher doesn't re-query the
--     remote MapServer on every call. Follows the multco/lane records_cache shape
--     so when a richer recordings endpoint surfaces (Linn County Clerk public
--     portal — verify queued), the swap is a fetcher-only change.
--
-- Both tables follow the established TERRA cache pattern: jsonb payload, fetched_at
-- timestamp, last_error column, ON CONFLICT upsert via the taxlot PK.
--
-- L3 permits cache for Linn shares the existing oregon_permits_cache table keyed
-- by (taxlot, county_fips) when the state Accela branch is hit. Albany city-jurisdiction
-- parcels route to permits.albanyoregon.gov (separate from state Accela; structured
-- scrape IN FLIGHT, deep link surfaced) — no new permits cache needed for v1 since
-- the Albany branch is deep-link-only at ingest time.

BEGIN;

CREATE TABLE IF NOT EXISTS linn_assessment_cache (
    taxlot          TEXT PRIMARY KEY,
    account_number  INTEGER,
    payload_json    JSONB,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_linn_assessment_cache_account
    ON linn_assessment_cache (account_number);
CREATE INDEX IF NOT EXISTS idx_linn_assessment_cache_fetched_at
    ON linn_assessment_cache (fetched_at);


CREATE TABLE IF NOT EXISTS linn_records_cache (
    taxlot          TEXT PRIMARY KEY,
    owner_name      TEXT,
    documents_json  JSONB,
    document_count  INTEGER,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_linn_records_cache_owner
    ON linn_records_cache (owner_name);
CREATE INDEX IF NOT EXISTS idx_linn_records_cache_fetched_at
    ON linn_records_cache (fetched_at);

COMMIT;
