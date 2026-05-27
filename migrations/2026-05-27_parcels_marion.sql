-- parcels_marion: Marion County (FIPS 047) taxlot polygons + native attribute fields.
--
-- Source: gis.co.marion.or.us/arcgis/rest/services/Public/Parcels/MapServer/0
--         (Marion County ArcGIS Server 10.91, public folder, single Parcels layer.)
-- Source SR: EPSG:102100 (Web Mercator) — server reprojects to 4326 on outSR=4326.
-- Storage SR: 4326 (matches parcels_deschutes / parcels_crook / parcels_multnomah).
-- Geometry: esriGeometryPolygon (single) — cast ST_Multi() on write so geom type
-- is MultiPolygon(4326) uniformly across the four-county set.
--
-- recordCount target at ingest authoring time (2026-05-27): ~135,000 parcels.
--
-- Schema note vs siblings:
--   Marion's parcel service ships an exceptionally rich inline attribute set —
--   ownership + situs + zoning + FULL CURRENT VALUATION (RMV land/imp/total +
--   assessed) + most-recent deed snapshot (INSTNUM/INSTTYPE/INSTDATE/SALEPRICE) +
--   building metrics (year/area/living) + deep-link URLs (REFLINK to MCASR,
--   MAPLINK to assessor tax map PDF). Like Multnomah, this lets Layer 2 reduce
--   to the deep-link surface + a time-series fetcher (IN FLIGHT, see KB_80).
--
-- county_fips column seeds the eventual unified `parcels` table migration
-- (queued post-3-county-expansion per earlier county migrations).

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_marion (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    taxlot TEXT NOT NULL,                      -- TAXLOT
    county_fips TEXT NOT NULL DEFAULT '047',

    -- Account / alternates
    taxacct TEXT,                              -- TAXACCT (Property ID — joins to mcasr.co.marion.or.us)
    other_accts TEXT,                          -- OTHERACCTS
    alt_taxlot TEXT,                           -- ALT_TAXLOT
    alt_taxacct TEXT,                          -- ALT_TAXACCT
    taxlot_ns TEXT,                            -- TAXLOTNS (no-spaces variant for joining)

    -- Address components + joined situs
    street_num TEXT,                           -- STREETNUM
    pre_dir TEXT,                              -- PRE_DIR
    street_name TEXT,                          -- STREETNAME
    street_type TEXT,                          -- STREETTYPE
    post_dir TEXT,                             -- POST_DIR
    unit_num TEXT,                             -- UNITNUM
    situs_address TEXT,                        -- SITUS (joined by source)
    situs_csz TEXT,                            -- SITUSCSZ (city, state, zip)

    -- Plat + legal
    plat_name TEXT,                            -- PLATNAME
    block TEXT,                                -- BLOCK
    lot TEXT,                                  -- LOT

    -- Acreage
    acres DOUBLE PRECISION,                    -- ACRES

    -- Most recent deed / sale (Layer 1 snapshot — full history IN FLIGHT)
    inst_num TEXT,                             -- INSTNUM
    inst_type TEXT,                            -- INSTTYPE
    inst_date TIMESTAMPTZ,                     -- INSTDATE
    sale_price BIGINT,                         -- SALEPRICE (integer in source)

    -- Ownership
    owner_name TEXT,                           -- OWNERNAME
    owner_addr TEXT,                           -- OWNERADDR
    owner_csz TEXT,                            -- OWNERCSZ

    -- Zoning is native (no PostGIS polygon join needed for Marion)
    zone_code TEXT,                            -- ZONECODE
    zone_auth TEXT,                            -- ZONEAUTH (city vs county vs joint)
    zone_web TEXT,                             -- ZONEWEB

    -- Improvements
    year_built INTEGER,                        -- YEARBUILT
    bldg_area INTEGER,                         -- BLDGAREA
    living_area INTEGER,                       -- LIVINGAREA

    -- Property classification
    prop_class TEXT,                           -- PROPCLASS
    prp_cls_desc TEXT,                         -- PRPCLSDESC

    -- Current roll valuation (Real Market Value family + assessed)
    rmv_land BIGINT,                           -- RMVLND
    rmv_imp BIGINT,                            -- RMVIMP
    rmv_total BIGINT,                          -- RMVTOTAL
    assd_val BIGINT,                           -- ASSDVAL

    -- Tax routing
    tax_code TEXT,                             -- TAXCODE
    city TEXT,                                 -- CITY
    school_dist TEXT,                          -- SCHLDIST
    fire_dist TEXT,                            -- FIREDIST

    -- Deep-link URLs (already verified live by source)
    reflink TEXT,                              -- REFLINK → MCASR PropertySummary
    maplink TEXT,                              -- MAPLINK → assessor tax-map PDF

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ                  -- LASTUPDATE from source
);

CREATE INDEX IF NOT EXISTS idx_parcels_marion_geom ON parcels_marion USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_marion_taxlot ON parcels_marion(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_marion_taxacct ON parcels_marion(taxacct);
CREATE INDEX IF NOT EXISTS idx_parcels_marion_owner_name ON parcels_marion(owner_name);
CREATE INDEX IF NOT EXISTS idx_parcels_marion_situs_address ON parcels_marion(situs_address);
CREATE INDEX IF NOT EXISTS idx_parcels_marion_zone_code ON parcels_marion(zone_code);
CREATE INDEX IF NOT EXISTS idx_parcels_marion_city ON parcels_marion(city);

COMMIT;
