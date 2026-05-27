-- Multnomah Layer 2 deep-records caches: three separate integrations, three cache tables.
--
-- Mirrors dial_permit_cache pattern (one row per taxlot, jsonb payload, 24h freshness).
-- Sharded by integration to keep schemas honest — payloads have nothing in common across
-- PropTax (valuation/tax-history snapshot), Records (recorded-document index since 2002),
-- and SAIL (surveys/plats/cadastral imagery).
--
-- 24h freshness rule lives in the fetcher code (fetched_at + interval check) — same as
-- dial_section_cache. force_refresh=True at the fetcher API bypasses the freshness gate.

BEGIN;

CREATE TABLE IF NOT EXISTS multco_proptax_cache (
    taxlot TEXT PRIMARY KEY,
    propid TEXT,
    payload_json JSONB,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_multco_proptax_cache_propid ON multco_proptax_cache(propid);

CREATE TABLE IF NOT EXISTS multco_records_cache (
    taxlot TEXT PRIMARY KEY,
    owner_name TEXT,
    documents_json JSONB,
    document_count INTEGER,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_multco_records_cache_owner ON multco_records_cache(owner_name);

CREATE TABLE IF NOT EXISTS multco_sail_cache (
    taxlot TEXT PRIMARY KEY,
    surveys_json JSONB,
    plats_json JSONB,
    cadastral_json JSONB,
    asset_count INTEGER,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

COMMIT;
