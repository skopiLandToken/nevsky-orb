-- parcels_josephine: Josephine County (FIPS 033) taxlot polygons + native attribute fields.
--
-- Source: gis.co.josephine.or.us/arcgis/rest/services/Assessor/Assessor_Taxlots/FeatureServer/0
--         (on-prem ArcGIS Server, layer 0 "Assessor Taxlots", 41,974 polygons as of ingest
--          authoring 2026-05-28). The hosted mirror at services3.arcgis.com/qwqIu50nUr6wRrbz
--          /arcgis/rest/services/JoCo_Taxlot/FeatureServer/0 carries the same schema but lags
--          ~200 features behind — we ingest from on-prem for the freshest count.
-- Source SR: 102100 (Web Mercator); we pull with outSR=4326 so server reprojects.
-- Storage SR: 4326 (matches every other parcels_* table for cross-county spatial unions).
-- Cast: ST_Multi() during ingest so geom is uniformly MultiPolygon(4326).
--
-- Schema note vs other parcels_* tables:
--   Josephine's taxlot service ships the RICHEST inline attribute surface TERRA has seen —
--   richer than Jackson, comparable to or exceeding Marion. The single Assessor_Taxlots
--   layer carries:
--     • Ownership: NAME (fee owner), mailing ADDR1/ADDR2/ADDR3 + City/State/ZIP/CSZ
--     • Situs:     SITUS / SITUS_CITY / SITUS_ST / SITUS_ZIP + parsed components
--                  (ST_NO, SITUS_PREF, ST_NAME, SITUS_SUFF, SITUS_SUF0)
--     • Account:   ACCOUNT (R-number), ACCTSTATUS, MapNum / MNX, township-range-section-QQ
--                  components (TWN/RNG/SEC/QQ), TAXLOT, LOT/Lot_1/BLOCK
--     • Valuation: APPR_VALUE, ASSD_VALUE, IMP_VALUE, LAND_APPR, LAND_MKT, RMV, MH_VALUE
--     • Acreage:   ACREAGE (assessor of record), LEGAL_ACRE, GIS_Acres
--     • Building:  YR_BLT, SQ_FT, LIVING_AREA, BEDRMS, BLDG_CLASS, MH_MAKE, COMP_MTL
--     • Tax:       CODE (tax code area), MAINT (maintenance area), NBHD, Exempt, Taxes, SPTB_CODES
--     • Zoning:    Zone (INLINE — no separate zoning service needed, unlike Jackson)
--     • Recent sale: SALE_DATE, SALE_PRICE, DEED_TYPE, INST_NO, SALE_TYPE
--     • Coordinates: Latitude, Longitude (assessor-curated centroid)
--
--   This means fetch_josephine_assessment AND fetch_josephine_records are BOTH SQL
--   pass-throughs over L1 inline — no scraper, no viewstate gating, no HTTP, no
--   separate zoning service. Marion/Jackson pattern repeated, only cleaner because
--   zoning is inline AND most-recent recorded sale (DEED_TYPE + INST_NO + SALE_DATE
--   + SALE_PRICE) is inline. Full chain-of-title is still Clerk's office surface
--   (deep-link IN FLIGHT).
--
-- county_fips column seeds the eventual unified `parcels` table migration (queued per
-- prior parcels_*.sql notes).

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_josephine (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    taxlot TEXT NOT NULL,                     -- MapNum (e.g., "32083100000100" — 14-char)
    county_fips TEXT NOT NULL DEFAULT '033',

    -- Map identifiers
    map_num TEXT,                              -- MapNum (canonical 14-char map+lot)
    mnx TEXT,                                  -- MNX (16-char extended)
    sd TEXT,                                   -- SD (school district name)
    twn TEXT,                                  -- TWN (township)
    rng TEXT,                                  -- RNG (range)
    sec TEXT,                                  -- SEC (section)
    qq TEXT,                                   -- QQ (quarter-quarter)
    taxlot_num TEXT,                           -- TAXLOT (6-char lot number)
    type TEXT,                                 -- TYPE
    loc_desc TEXT,                             -- LOC_DESC

    -- Subdivision
    lot TEXT,                                  -- LOT
    lot_1 TEXT,                                -- Lot_1
    block TEXT,                                -- BLOCK

    -- Assessor account
    account TEXT,                              -- ACCOUNT (R-number, e.g., "R300001")
    acct_status TEXT,                          -- ACCTSTATUS

    -- Native ownership
    owner_name TEXT,                           -- NAME

    -- Mailing address
    mailing_address TEXT,                      -- ADDRESS (combined)
    mailing_addr1 TEXT,                        -- ADDR1
    mailing_addr2 TEXT,                        -- ADDR2
    mailing_addr3 TEXT,                        -- ADDR3
    mailing_city TEXT,                         -- City
    mailing_state TEXT,                        -- State
    mailing_zip TEXT,                          -- ZIP
    mailing_csz TEXT,                          -- CSZ (City,State,Zip combined)

    -- Situs (native, pre-parsed)
    situs_address TEXT,                        -- SITUS
    situs_city TEXT,                           -- SITUS_CITY
    situs_state TEXT,                          -- SITUS_ST
    situs_zip TEXT,                            -- SITUS_ZIP
    st_no TEXT,                                -- ST_NO (street number)
    situs_pref TEXT,                           -- SITUS_PREF (directional prefix)
    st_name TEXT,                              -- ST_NAME
    situs_suff TEXT,                           -- SITUS_SUFF (street suffix)
    situs_suf0 INTEGER,                        -- SITUS_SUF0 (suffix code)

    -- Valuation
    appr_value DOUBLE PRECISION,               -- APPR_VALUE
    assd_value DOUBLE PRECISION,               -- ASSD_VALUE
    imp_value DOUBLE PRECISION,                -- IMP_VALUE
    land_appr DOUBLE PRECISION,                -- LAND_APPR
    land_mkt DOUBLE PRECISION,                 -- LAND_MKT
    rmv DOUBLE PRECISION,                      -- RMV (Real Market Value total)
    mh_value DOUBLE PRECISION,                 -- MH_VALUE (manufactured home value)

    -- Acreage (three sources — ACREAGE is assessor of record)
    acreage DOUBLE PRECISION,                  -- ACREAGE
    legal_acre DOUBLE PRECISION,               -- LEGAL_ACRE
    gis_acres DOUBLE PRECISION,                -- GIS_Acres

    -- Classification + tax
    prop_class TEXT,                           -- PROP_CLASS
    bldg_class TEXT,                           -- BLDG_CLASS
    code TEXT,                                 -- CODE (tax code area)
    maint TEXT,                                -- MAINT (maintenance area)
    nbhd TEXT,                                 -- NBHD (neighborhood)
    sptb_codes TEXT,                           -- SPTB_CODES (special tax / billing codes)
    exempt INTEGER,                            -- Exempt
    taxes DOUBLE PRECISION,                    -- Taxes

    -- Building
    yr_blt DOUBLE PRECISION,                   -- YR_BLT
    sq_ft DOUBLE PRECISION,                    -- SQ_FT
    living_area DOUBLE PRECISION,              -- LIVING_AREA
    bedrms DOUBLE PRECISION,                   -- BEDRMS
    mh_make TEXT,                              -- MH_MAKE (manufactured home make)
    comp_mtl INTEGER,                          -- COMP_MTL

    -- Zoning (INLINE — no separate zoning service)
    zone TEXT,                                 -- Zone

    -- Most-recent recorded sale (inline)
    sale_date TIMESTAMPTZ,                     -- SALE_DATE
    sale_price DOUBLE PRECISION,               -- SALE_PRICE
    deed_type TEXT,                            -- DEED_TYPE
    inst_no TEXT,                              -- INST_NO (instrument number)
    sale_type TEXT,                            -- SALE_TYPE

    -- Assessor-curated centroid (sometimes differs from geom centroid)
    asr_latitude DOUBLE PRECISION,             -- Latitude
    asr_longitude DOUBLE PRECISION,            -- Longitude

    -- Shape metrics + geometry
    shape_area DOUBLE PRECISION,               -- Shape__Area
    shape_length DOUBLE PRECISION,             -- Shape__Length
    geom GEOMETRY(MultiPolygon, 4326),

    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_parcels_josephine_geom         ON parcels_josephine USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_josephine_taxlot       ON parcels_josephine(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_josephine_map_num      ON parcels_josephine(map_num);
CREATE INDEX IF NOT EXISTS idx_parcels_josephine_account      ON parcels_josephine(account);
CREATE INDEX IF NOT EXISTS idx_parcels_josephine_owner_name   ON parcels_josephine(owner_name);
CREATE INDEX IF NOT EXISTS idx_parcels_josephine_situs        ON parcels_josephine(situs_address);
CREATE INDEX IF NOT EXISTS idx_parcels_josephine_situs_city   ON parcels_josephine(situs_city);
CREATE INDEX IF NOT EXISTS idx_parcels_josephine_zone         ON parcels_josephine(zone);

COMMIT;
