-- parcels_douglas: Douglas County (FIPS 019) taxlot polygons + Marion-style inline attribute rows.
--
-- Source: gis.co.douglas.or.us/server/rest/services/Parcel/Parcels/FeatureServer/0
--         (Douglas County ArcGIS Server 11.5, public folder, "Parcels" feature service
--          under the AGOL org "DCOR" / dcor.maps.arcgis.com.) Read-only Query + Extract.
-- Source SR: EPSG:2270 (Oregon SP South NAD83 HARN, ftUS). Server reprojects on
--            outSR=4326 / f=geojson.
-- Storage SR: 4326 (matches sibling parcels_* tables).
-- Geometry: esriGeometryPolygon (single). ST_Multi() on write so geom is
--           MultiPolygon(4326) uniformly across the county set.
--
-- recordCount target at ingest authoring time (2026-05-28): 68,973 polygons.
-- NOT owner-exploded — Douglas ships one row per parcel (Marion / Jackson / Polk pattern,
-- distinct from Benton's TaxlotOwners which repeats geom once per Party_Name).
--
-- Schema note vs siblings:
--   Douglas's Parcels service ships a rich joined Marion-style attribute row — owner
--   (NAME, 350 char), mailing block (ADDR1/2/3/CSZ), situs (SitusAddress/SitusCSZ),
--   account (TAXID, PROP_ID, ALT_ACCTNUM), property class (PROPCLASS), tax code area
--   (CODEAREA), full valuation breakdown (LAND_MKT_VALUE / IMPRV_VALUE / MARKET_VALUE /
--   ASSD_VALUE), acreage (ACCT_ACREAGE / MTLACREAGE / TotalAcreage), legal description
--   (LEGAL, unbounded), most-recent instrument # (INST_NO, also rolls 500-char), and
--   sale date (SaleDate text) all inline. Plus SPECINTEREST flag (timber/farm/etc.
--   special-assessment marker), BLOCK / LOT for subdivision parcels, OWNER_ID integer,
--   NBHDCODE neighborhood, MAINTAREA maint district, LOC_CODE situs code.
--
--   This means fetch_douglas_assessment (Layer 2) is a SQL pass-through over
--   parcels_douglas + spatial join against douglas_zoning for DC_ZONE (Planning/Zoning
--   FeatureServer, separate service) — NO scraper, NO viewstate gating. Marion /
--   Jackson pattern repeated.
--
--   Douglas zoning includes F1 / F2 / F3 / FF / FG (Forest / Forest-Farm / Farm-Grazing)
--   and AW (Agricultural Watershed / EFU-equivalent) which capture timber-land + farm
--   special assessments — Tier 2 timber-MP marketing angle differentiator per
--   KB_75 (TERRA-CLOSER-01). Forest-classified parcels also show in PROPCLASS prefix
--   (9xx = forest / timber / O&C grant land class).
--
-- county_fips column seeds the eventual unified `parcels` table migration (queued post
-- 3-county-expansion per [[project-yindo-parallel-sessions]] memory).

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_douglas (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    county_fips TEXT NOT NULL DEFAULT '019',

    -- Account / canonical taxlot identifiers
    taxid TEXT NOT NULL,                       -- TAXID (20-char canonical taxlot, e.g. "19080000100")
    prop_id TEXT,                              -- PROP_ID (10-char property ID, e.g. "R10007")
    alt_acctnum TEXT,                          -- ALT_ACCTNUM (legacy alternate account #)
    owner_id INTEGER,                          -- OWNER_ID (integer joined to assessor owner table)

    -- Ownership (single row per parcel — current fee owner of record)
    owner_name TEXT,                           -- NAME (350 char — primary fee owner name)

    -- Mailing address
    mail_addr1 TEXT,                           -- ADDR1
    mail_addr2 TEXT,                           -- ADDR2
    mail_addr3 TEXT,                           -- ADDR3
    mail_csz TEXT,                             -- CSZ (City, State, Zip combined)

    -- Situs (parcel street address)
    situs_address TEXT,                        -- SitusAddress (street + number)
    situs_csz TEXT,                            -- SitusCSZ (e.g. "Westlake, OR  97493")

    -- Property classification
    propclass TEXT,                            -- PROPCLASS (e.g. "100" res, "970" forest)
    specinterest TEXT,                         -- SPECINTEREST (special-assessment flag — timber / farm / etc.)
    codearea TEXT,                             -- CODEAREA (tax code area)
    loc_code TEXT,                             -- LOC_CODE
    maintarea TEXT,                            -- MAINTAREA
    nbhdcode TEXT,                             -- NBHDCODE (neighborhood code)

    -- Subdivision references
    block TEXT,                                -- BLOCK
    lot TEXT,                                  -- LOT

    -- Valuation (assessor of record)
    assd_value DOUBLE PRECISION,               -- ASSD_VALUE (taxable assessed value)
    land_mkt_value DOUBLE PRECISION,           -- LAND_MKT_VALUE (real-market land)
    imprv_value DOUBLE PRECISION,              -- IMPRV_VALUE (real-market improvements)
    market_value DOUBLE PRECISION,             -- MARKET_VALUE (real-market total)

    -- Acreage (three flavors from source — preserve all for reconciliation)
    acct_acreage DOUBLE PRECISION,             -- ACCT_ACREAGE (account-of-record)
    mtl_acreage DOUBLE PRECISION,              -- MTLACREAGE (material acreage)
    total_acreage DOUBLE PRECISION,            -- TotalAcreage (polygon GIS-calculated)

    -- Last instrument + sale snapshot
    inst_no TEXT,                              -- INST_NO (most recent instrument; e.g. "2000-3417")
    sale_date TEXT,                            -- SaleDate (text, MM-DD-YYYY or YYYY-MM-DD per source)

    -- Legal description (unbounded length — keep as TEXT)
    legal TEXT,                                -- LEGAL

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- No last_modified field on source — daily delta requires OBJECTID range scan
    -- or full re-ingest. ON CONFLICT (objectid) DO UPDATE makes either approach
    -- idempotent.
);

CREATE INDEX IF NOT EXISTS idx_parcels_douglas_geom ON parcels_douglas USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_douglas_taxid ON parcels_douglas(taxid);
CREATE INDEX IF NOT EXISTS idx_parcels_douglas_prop_id ON parcels_douglas(prop_id);
CREATE INDEX IF NOT EXISTS idx_parcels_douglas_owner_name ON parcels_douglas(owner_name);
CREATE INDEX IF NOT EXISTS idx_parcels_douglas_propclass ON parcels_douglas(propclass);
CREATE INDEX IF NOT EXISTS idx_parcels_douglas_codearea ON parcels_douglas(codearea);

-- Zoning polygons (Planning/Zoning FeatureServer, 2302 polygons, DC_ZONE only).
-- F1/F2/F3/FF/FG = forest variants (timber MP differentiator); AW = agricultural
-- watershed (EFU equivalent); 1R/5R = Roseburg residential overlays; etc.
CREATE TABLE IF NOT EXISTS douglas_zoning (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    zone_code TEXT,                            -- DC_ZONE (16-char)
    src_id INTEGER,                            -- ID (source row id; not meaningful)
    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_douglas_zoning_geom ON douglas_zoning USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_douglas_zoning_code ON douglas_zoning(zone_code);

COMMIT;
