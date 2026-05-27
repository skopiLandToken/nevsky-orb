-- Clackamas Layer 2 + Layer 3 caches: assessment, records, permits.
--
-- Mirrors wash_*_cache pattern (one row per taxlot, jsonb payload, 24h freshness).
-- Sharded per integration so payload shapes stay honest:
--   clack_assessment_cache — owner/situs/valuation/sale snapshot (currently
--                            IN FLIGHT: ascendweb.clackamas.us is ASP.NET WebForms
--                            with __VIEWSTATE gating — confirmed via probe;
--                            bare GET to default.aspx returns viewstate but the
--                            POST that resolves PARCEL_NUMBER → property detail
--                            requires the full ClientState payload).
--   clack_records_cache    — recorded documents (deeds/mortgages/liens) via the
--                            Clackamas County Clerk Recording Division. No public
--                            search portal located — deep link to clackamas.us/recording
--                            until that surface is identified or paid tier wired.
--   clack_permits_cache    — Accela ACA at aca-prod.accela.com/clackamas (county-
--                            direct Accela cloud instance, NOT the state oregon.gov
--                            Accela; recon confirmed via clackamas.us/building which
--                            routes "log in to our permit system" to aca-prod). So
--                            this cache is separate from oregon_permits_cache and
--                            wash_permits_cache — three different Accela tenants
--                            we now cache. The Development Direct (Avolve cloud)
--                            surface at clackamas-or-us.avolvecloud.com handles new
--                            permit submissions but isn't the search surface.
--
-- 24h freshness rule lives in the fetcher code (fetched_at + interval check) — same as
-- dial_section_cache / multco_proptax_cache / wash_*_cache. force_refresh=True bypasses.

BEGIN;

CREATE TABLE IF NOT EXISTS clack_assessment_cache (
    taxlot TEXT PRIMARY KEY,
    parcel_number TEXT,                  -- Clackamas's 8-char account number
    payload_json JSONB,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_clack_assessment_cache_parcel_number ON clack_assessment_cache(parcel_number);

CREATE TABLE IF NOT EXISTS clack_records_cache (
    taxlot TEXT PRIMARY KEY,
    owner_name TEXT,
    documents_json JSONB,
    document_count INTEGER,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_clack_records_cache_owner ON clack_records_cache(owner_name);

CREATE TABLE IF NOT EXISTS clack_permits_cache (
    taxlot TEXT PRIMARY KEY,
    jurisdiction TEXT,                   -- 'Clackamas County (unincorporated)' | 'Oregon City' | 'Lake Oswego' | 'West Linn' | 'Happy Valley' | 'Milwaukie' | 'other'
    permits_json JSONB,
    permit_count INTEGER,
    aca_search_url TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_clack_permits_cache_jurisdiction ON clack_permits_cache(jurisdiction);

COMMIT;
