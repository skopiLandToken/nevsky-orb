-- Benton Layer 2 caches: assessment + records.
--
-- Mirrors lane_*_cache / wash_*_cache pattern (one row per taxlot, jsonb payload,
-- 24h freshness, last_error column for honest IN FLIGHT framing). Sharded per
-- integration so payload shapes stay aligned with each upstream source.
--
--   benton_assessment_cache — owner / situs / mail / portal-deeplink snapshot.
--                             L1 ships owner_name (aggregated) + situs inline,
--                             so this fetcher reads from parcels_benton and
--                             returns a SQL pass-through structured shape +
--                             the assessment portal landing URL. Real per-parcel
--                             deeplink IN FLIGHT pending bcaps form params reverse
--                             engineering (the WordPress assessment site uses a
--                             POST-only form with no GET parameter surface).
--
--   benton_records_cache    — Records & Elections department portal at
--                             records.co.benton.or.us/Recording/. Public search
--                             UI exists; structured deeplink + scrape IN FLIGHT.
--
-- Permits live in the SHARED oregon_permits_cache table (Benton participates in
-- the state ePermitting portal at aca-oregon.accela.com/oregon — confirmed via
-- cd.bentoncountyor.gov/electronic-permitting linking directly to that surface).
-- No benton_permits_cache table; fetch_benton_permits is a one-line wrapper
-- around fetch_county_permits('003').

BEGIN;

CREATE TABLE IF NOT EXISTS benton_assessment_cache (
    taxlot TEXT PRIMARY KEY,
    account_num TEXT,
    payload_json JSONB,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_benton_assessment_cache_account
    ON benton_assessment_cache(account_num);

CREATE TABLE IF NOT EXISTS benton_records_cache (
    taxlot TEXT PRIMARY KEY,
    owner_name TEXT,
    documents_json JSONB,
    document_count INTEGER,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

COMMIT;
