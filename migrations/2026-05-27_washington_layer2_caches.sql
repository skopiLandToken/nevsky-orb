-- Washington Layer 2 + Layer 3 caches: assessment, records, permits.
--
-- Mirrors multco_*_cache pattern (one row per taxlot, jsonb payload, 24h freshness).
-- Sharded per integration so payload shapes stay honest:
--   wash_assessment_cache — owner/situs/valuation/sale snapshot (currently IN FLIGHT:
--                          washcotax.co.washington.or.us is a DotNetNuke + ASP.NET
--                          WebForms surface with viewstate gating; bare GET to the
--                          property-detail page is captcha-free but the TLNO →
--                          R-number lookup requires a viewstate POST).
--   wash_records_cache   — recorded documents (deeds/mortgages/liens) via the
--                          Washington County Clerk recordings surface. Deep-link
--                          only until that surface is scraped or paid tier wired.
--   wash_permits_cache   — Accela ACA at permits.washingtoncountyor.gov (county-
--                          direct instance, NOT the state oregon.gov Accela. So this
--                          cache is separate from oregon_permits_cache which keys on
--                          state-portal participants by FIPS).
--
-- 24h freshness rule lives in the fetcher code (fetched_at + interval check) — same as
-- dial_section_cache / multco_proptax_cache. force_refresh=True bypasses the gate.

BEGIN;

CREATE TABLE IF NOT EXISTS wash_assessment_cache (
    taxlot TEXT PRIMARY KEY,
    r_number TEXT,                       -- Washington A&T's PropertyQuickRefID (e.g. 'R429432')
    payload_json JSONB,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_wash_assessment_cache_r_number ON wash_assessment_cache(r_number);

CREATE TABLE IF NOT EXISTS wash_records_cache (
    taxlot TEXT PRIMARY KEY,
    owner_name TEXT,
    documents_json JSONB,
    document_count INTEGER,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_wash_records_cache_owner ON wash_records_cache(owner_name);

CREATE TABLE IF NOT EXISTS wash_permits_cache (
    taxlot TEXT PRIMARY KEY,
    jurisdiction TEXT,                   -- 'Beaverton' | 'Hillsboro' | 'Tigard' | 'Washington County (unincorporated)' | 'other'
    permits_json JSONB,
    permit_count INTEGER,
    aca_search_url TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_wash_permits_cache_jurisdiction ON wash_permits_cache(jurisdiction);

COMMIT;
