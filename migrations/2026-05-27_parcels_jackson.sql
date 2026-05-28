-- parcels_jackson: Jackson County (FIPS 029) taxlot polygons + native attribute fields.
--
-- Source: spatial.jacksoncountyor.gov/arcgis/rest/services/OpenData/ReferenceData/MapServer/3
--         (on-prem ArcGIS Server, layer 3 "Tax Lots", 104,751 polygons as of ingest authoring 2026-05-27).
-- Source SR: EPSG:2270 (Oregon SP South NAD83 HARN ftUS); we pull with outSR=4326 so server reprojects.
-- Storage SR: 4326 (matches every other parcels_* table for cross-county spatial unions).
-- Cast: ST_Multi() during ingest so geom is uniformly MultiPolygon(4326).
--
-- Schema note vs other parcels_* tables:
--   Jackson's taxlot service ships rich inline attribute data — comparable to Multnomah and
--   Marion in completeness — including FEEOWNER, SITEADD (situs), full mailing address,
--   ACCOUNT (assessor account ID), real-market values (LANDVALUE / IMPVALUE), assessed
--   values (ASSESSLAND / ASSESSIMP), acreage, prop_class, building code, year built,
--   tax_code, commercial sqft. ZoningDistricts is a SEPARATE FeatureServer on the same
--   spatial.jacksoncountyor.gov host and is spatially joined at lookup time (no native
--   zoning column on the taxlot layer).
--
--   This means fetch_jackson_assessment (Layer 2) is a SQL pass-through over L1 inline
--   data with a spatial join against jackson_zoning (or the live ZoningDistricts service)
--   — NO scraper, NO viewstate gating. Marion pattern repeated.
--
--   Permits (Layer 3) are ALSO publicly exposed via spatial.jacksoncountyor.gov as a point
--   FeatureServer (DevServ/Permit, layers 0 and 1) with 243k building + 40k land-use
--   permits, each carrying a deep link to ACA-Oregon Accela CapDetail. So Jackson Layer 3
--   does NOT use fetch_county_permits (the Accela viewstate-gated deep-link-only fallback);
--   instead, fetch_jackson_permits hits the GIS API directly with a spatial filter over
--   the parcel polygon and returns real structured permits.
--
-- county_fips column seeds the eventual unified `parcels` table migration (queued per
-- prior parcels_*.sql notes).

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_jackson (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    taxlot TEXT NOT NULL,                     -- MAPLOT (e.g., "372W04C 2400")
    county_fips TEXT NOT NULL DEFAULT '029',

    -- Map identifiers — Jackson ships three flavors:
    --   MAPNUMBER  = formatted township-range-section          (e.g., "37-2W-04C")
    --   MAPNUM     = canonical compressed                       (e.g., "372W04C")
    --   TM_MAPLOT  = township-range-section + taxlot for display
    map_number TEXT,
    map_num TEXT,
    tm_maplot TEXT,

    -- Assessor account
    account INTEGER,
    lottype TEXT,                              -- LOTTYPE
    prop_class SMALLINT,                       -- PROPCLASS
    tax_code SMALLINT,                         -- TAXCODE (tax code area)

    -- Native ownership (would be a DIAL fetch on Deschutes)
    owner_name TEXT,                           -- FEEOWNER (primary fee owner)
    contract_buyer TEXT,                       -- CONTRACT (contract buyer name when applicable)
    in_care_of TEXT,                           -- INCAREOF

    -- Mailing address
    mailing_address_1 TEXT,                    -- ADDRESS1
    mailing_address_2 TEXT,                    -- ADDRESS2
    mailing_city TEXT,                         -- CITY
    mailing_state TEXT,                        -- STATE
    mailing_zip TEXT,                          -- ZIPCODE (stored as text to preserve leading zeros)

    -- Situs address (native pre-joined)
    situs_address TEXT,                        -- SITEADD
    address_num TEXT,                          -- ADDRESSNUM
    street_name TEXT,                          -- STREETNAME

    -- Improvements
    build_code SMALLINT,                       -- BUILDCODE
    year_built SMALLINT,                       -- YEARBLT
    comm_sqft INTEGER,                         -- COMMSQFT
    lot_depth SMALLINT,                        -- LOTDEPTH
    lot_width SMALLINT,                        -- LOTWIDTH

    -- Acreage (single source on Jackson — ACREAGE is assessor of record)
    acreage DOUBLE PRECISION,                  -- ACREAGE
    gis_area DOUBLE PRECISION,                 -- GIS_AREA (sqft from the GIS layer; kept for reconciliation)

    -- Current real-market values (RMV)
    land_value INTEGER,                        -- LANDVALUE  (RMV land)
    imp_value INTEGER,                         -- IMPVALUE   (RMV improvements)

    -- Assessed values (Measure-50)
    assess_land INTEGER,                       -- ASSESSLAND
    assess_imp INTEGER,                        -- ASSESSIMP

    -- Maintenance / scheduling / neighborhood (assessor internal codes; kept for joins)
    maintenance SMALLINT,                      -- MAINTENANC
    schedule_co SMALLINT,                      -- SCHEDULECO
    neighborhood SMALLINT,                     -- NEIGHBORHO

    -- Shape metrics + geometry
    shape_length DOUBLE PRECISION,             -- Shape.STLength()
    shape_area DOUBLE PRECISION,               -- Shape.STArea()
    geom GEOMETRY(MultiPolygon, 4326),

    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_parcels_jackson_geom         ON parcels_jackson USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_jackson_taxlot       ON parcels_jackson(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_jackson_account      ON parcels_jackson(account);
CREATE INDEX IF NOT EXISTS idx_parcels_jackson_owner_name   ON parcels_jackson(owner_name);
CREATE INDEX IF NOT EXISTS idx_parcels_jackson_situs        ON parcels_jackson(situs_address);
CREATE INDEX IF NOT EXISTS idx_parcels_jackson_map_num      ON parcels_jackson(map_num);


-- =============================================================================
-- jackson_zoning — Jackson County ZoningDistricts polygons (cached snapshot)
-- =============================================================================
-- Spatially joined against parcels_jackson at lookup time. Updates daily via the
-- same delta job pattern as parcels (jackson_zoning is small — a few thousand
-- polygons — so full re-ingest is cheaper than diff logic).
CREATE TABLE IF NOT EXISTS jackson_zoning (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    zone_code TEXT,                            -- ZONECLASS
    zone_desc TEXT,                            -- ZONEDESC
    comp_plan_des TEXT,                        -- COMPPLANDES (comprehensive plan designation)
    base_elev DOUBLE PRECISION,
    height_max DOUBLE PRECISION,
    last_update TIMESTAMPTZ,
    last_editor TEXT,
    shape_area DOUBLE PRECISION,
    shape_length DOUBLE PRECISION,
    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jackson_zoning_geom ON jackson_zoning USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_jackson_zoning_code ON jackson_zoning(zone_code);

COMMIT;
