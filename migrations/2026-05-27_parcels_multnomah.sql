-- parcels_multnomah: Multnomah County (FIPS 051) taxlot polygons + native attribute fields.
--
-- Source: services5.arcgis.com/x7DNZL1YqNQVNykA/.../Multnomah_County_Taxlot_Parcels/FeatureServer/0
--         (ArcGIS Online hosted feature service, surfaced via gis-multco.opendata.arcgis.com Hub.)
-- Source geometry: esriGeometryPolygon (single Polygon).
-- Storage SR: 4326 (matches parcels_deschutes + parcels_crook for cross-county spatial unions).
-- Cast: ST_Multi() during ingest so geom type is MultiPolygon(4326) uniformly across counties.
--
-- recordCount at ingest authoring time (2026-05-27): 284,293 parcels.
--
-- Schema note vs parcels_crook (the newer template):
--   Multnomah's taxlot service ships the RICHEST inline attribute set of any Oregon county we've seen:
--   owner names, situs address (already-joined SITUSADDR field), zoning, full valuations
--   (ROLLLAND/ROLLIMP/ROLLM50), sale price + sale date, deed type/date/instrument number,
--   property class, year built, square footage. This means Layer 2 PropTax can be scoped down
--   to time-series + tax-history-only — the snapshot is already inline at Layer 1.
--
-- county_fips column is the seed for the eventual unified `parcels` table migration
-- (queued post-3-county-expansion per parcels_crook migration note).

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_multnomah (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    taxlot TEXT NOT NULL,                      -- MAPTAXLOT
    county_fips TEXT NOT NULL DEFAULT '051',

    -- Native identifiers
    propid TEXT,                               -- PROPID (Multnomah's account-level ID; key for deep-records URLs)
    alt_account_num TEXT,                      -- ALTACCTNUM

    -- Native ownership + addressing (would be a DIAL fetch on Deschutes)
    owner_name TEXT,                           -- NAME
    owner_name_2 TEXT,                         -- NAME2
    mailing_address TEXT,                      -- ADDR1
    mailing_address_2 TEXT,                    -- ADDR2
    mailing_city TEXT,                         -- CITY
    mailing_state TEXT,                        -- STATE
    mailing_zip TEXT,                          -- ZIP

    situs_address TEXT,                        -- SITUSADDR (pre-joined by source)
    situs_city TEXT,                           -- SITUSCITY
    situs_state TEXT,                          -- SITUSSTATE
    situs_zip TEXT,                            -- SITUSZIP

    -- Map / legal
    map_id TEXT,                               -- MAPID
    legal_desc TEXT,                           -- LEGAL
    tract_lot TEXT,                            -- TRACTLOT
    block TEXT,                                -- BLOCK
    add_legal TEXT,                            -- ADDLEGAL
    township_range TEXT,                       -- TownshipRange
    assessor_map TEXT,                         -- AssessorMap

    -- Account class / tax routing
    loc_code TEXT,                             -- LOC_CODE
    account_status TEXT,                       -- ACCOUNT_STATUS
    levy_code TEXT,                            -- LEVYCODE
    nbo_code TEXT,                             -- NBOCODE
    imp_count TEXT,                            -- IMP_COUNT (text in source despite name)
    prop_class TEXT,                           -- PROPCLASS
    prop_code TEXT,                            -- PROP_CODE

    -- Native zoning (no PostGIS join needed for Multnomah)
    zone_code TEXT,                            -- ZONING

    -- Most recent deed / sale (snapshot — full history requires Layer 2 Records integration)
    deed_type TEXT,                            -- DEED_TYPE
    deed_date TIMESTAMPTZ,                     -- DEED_DATE
    inst_num TEXT,                             -- INST_NUM
    sale_price DOUBLE PRECISION,               -- SALE_PRICE
    sale_date TIMESTAMPTZ,                     -- SALE_DATE
    exemption TEXT,                            -- EXEMPTION

    -- Acreage + improvements
    size_acres DOUBLE PRECISION,               -- SIZEACRES
    size_sqft BIGINT,                          -- SIZESQFT
    imp_type TEXT,                             -- IMPTYPE
    act_year_built INTEGER,                    -- ACTYEARBUILT
    main_area INTEGER,                         -- MAINAREA
    units INTEGER,                             -- UNITS
    main_sqft INTEGER,                         -- MAIN_SQFT

    -- Roll values (current tax roll snapshot)
    roll_year SMALLINT,                        -- ROLLYEAR
    roll_land DOUBLE PRECISION,                -- ROLLLAND
    roll_imp DOUBLE PRECISION,                 -- ROLLIMP
    roll_m50 DOUBLE PRECISION,                 -- ROLLM50 (Measure 50 assessed value)

    -- Shape metrics + geometry
    shape_length DOUBLE PRECISION,             -- Shape__Length
    shape_area DOUBLE PRECISION,               -- Shape__Area
    geom GEOMETRY(MultiPolygon, 4326),

    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_parcels_multnomah_geom ON parcels_multnomah USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_multnomah_taxlot ON parcels_multnomah(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_multnomah_propid ON parcels_multnomah(propid);
CREATE INDEX IF NOT EXISTS idx_parcels_multnomah_zone_code ON parcels_multnomah(zone_code);
CREATE INDEX IF NOT EXISTS idx_parcels_multnomah_owner_name ON parcels_multnomah(owner_name);
CREATE INDEX IF NOT EXISTS idx_parcels_multnomah_situs_address ON parcels_multnomah(situs_address);
CREATE INDEX IF NOT EXISTS idx_parcels_multnomah_roll_year ON parcels_multnomah(roll_year);

COMMIT;
