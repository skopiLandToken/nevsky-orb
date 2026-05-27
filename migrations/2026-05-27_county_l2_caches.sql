-- L2 fetcher cache tables — assessment + permits.
--
-- Two tables, two access patterns:
--
--   crook_tax_card_cache: 1:1 with a Crook taxlot. PDF extraction is Crook-specific
--     because tax cards are county-specific PDFs at predictable URLs. If Jefferson or
--     Lane publish similar tax cards we'd add a sibling table, NOT generalize this one
--     (PDF parsing logic differs per county template).
--
--   oregon_permits_cache: keyed by (taxlot, county_fips). Oregon ePermitting (Accela)
--     is a single state-wide portal; same scraper code handles every participating
--     county. The county_fips column is what makes this reusable for Jefferson, Lane,
--     and any Tier 3/4 county that opts into the state system.
--
-- Both tables follow the DIAL cache pattern: jsonb payload, fetched_at timestamp,
-- last_error column for cached failure state, ON CONFLICT upsert via the unique key.

BEGIN;

-- =============================================================================
-- crook_tax_card_cache — parsed PDF tax-card data for Crook parcels
-- =============================================================================
CREATE TABLE IF NOT EXISTS crook_tax_card_cache (
    taxlot          TEXT PRIMARY KEY,
    pdf_url         TEXT NOT NULL,
    parsed_json     JSONB,           -- {owner, account, prop_class, values: {real_market, assessed, exemptions, taxable, by_year: [...]}, ...}
    raw_text        TEXT,            -- full PDF text, useful for diagnostic + future re-parse without re-download
    page_count      INTEGER,
    pdf_etag        TEXT,            -- gis.crookcountyor.gov serves a stable ETag; cheap pre-check before re-parse
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_crook_tax_card_cache_fetched_at
    ON crook_tax_card_cache (fetched_at);


-- =============================================================================
-- oregon_permits_cache — Accela ACA scrape results, keyed by (taxlot, county)
-- =============================================================================
-- Scope note: Oregon's state ePermitting system (aca-oregon.accela.com) only holds
-- permits for jurisdictions that participate in the state portal. Crook participates;
-- Jefferson and Lane both have local + state-portal options. Counties NOT in the
-- state system (Deschutes is the major counter-example — uses DIAL natively) won't
-- have rows here, and the fetcher returns a structured "county does not participate"
-- result rather than an empty scrape.
CREATE TABLE IF NOT EXISTS oregon_permits_cache (
    id              BIGSERIAL PRIMARY KEY,
    taxlot          TEXT NOT NULL,
    county_fips     TEXT NOT NULL,
    permits_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
    permit_count    INTEGER NOT NULL DEFAULT 0,
    aca_search_url  TEXT,            -- deep link to the ACA result page so user can verify
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error      TEXT,
    UNIQUE (taxlot, county_fips)
);

CREATE INDEX IF NOT EXISTS idx_oregon_permits_cache_fetched_at
    ON oregon_permits_cache (fetched_at);
CREATE INDEX IF NOT EXISTS idx_oregon_permits_cache_county
    ON oregon_permits_cache (county_fips);

COMMIT;
