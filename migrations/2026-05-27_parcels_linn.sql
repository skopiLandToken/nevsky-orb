-- parcels_linn: Linn County (FIPS 043) taxlot polygons.
--
-- Source: gis.co.linn.or.us/arcgis/rest/services/AssessmentTax/ORMAP_public/MapServer/3
--         (Linn County ArcGIS Server 11.5, AssessmentTax folder, ORMAP_public/Taxlot layer.)
-- Source SR: EPSG:2913 (Oregon Lambert ft) — server reprojects to 4326 on outSR=4326.
-- Storage SR: 4326 (matches the rest of the TERRA county fleet).
-- Geometry: esriGeometryPolygon (single) — cast ST_Multi() on write so geom type
-- is MultiPolygon(4326) uniformly across counties.
--
-- recordCount target at ingest authoring time (2026-05-27): ~55,754 total taxlot
-- polygons (~54,837 real taxlots; the remainder are ROADS/WATER/CITY pseudo-lots
-- that Linn ORMAP retains for cartographic completeness — we ingest all of them
-- and let downstream callers filter on `Taxlot NOT IN ('ROADS','WATER','CITY')`
-- when only "real" parcels are wanted; lookup_parcel_by_point will naturally hit
-- the most-specific polygon when a pin lands inside one).
--
-- Schema note vs siblings:
--   Linn's ORMAP_public layer ships cartographic taxlot polygons + ORMAP keys
--   (Taxlot/MapTaxlot/ORTaxlot/MapNumber/ORMapNum) + acreage (TaxlotAcre /
--   MapAcres / CalculatedAcres) + ORMAP audit fields (Source/SourceType/
--   ReliaCode/Validation/AutoDate). It does NOT include owner / situs / zoning
--   / valuation / sale history inline — those live in the Assessment & Tax
--   roll system and the public `pub_sales` MapServer (gis.co.linn.or.us/
--   .../public/pub_sales/MapServer/0). So Linn follows the
--   Washington/Clackamas pattern: L1 is boundaries-only, L2 (fetch_linn_*)
--   resolves owner/situs/sale/zoning on demand from pub_sales + assessor portal.
--
-- county_fips column seeds the eventual unified `parcels` table migration
-- (queued post-3-county-expansion per earlier county migrations).

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_linn (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    taxlot TEXT NOT NULL,                       -- Taxlot (5-char suffix: 00100, ROADS, etc.)
    county_fips TEXT NOT NULL DEFAULT '043',

    -- ORMAP keys (the canonical Oregon ORMAP identifiers)
    map_taxlot TEXT,                            -- MapTaxlot (e.g. "10S03W04  ROADS")
    or_taxlot TEXT,                             -- ORTaxlot ("2210.00S03.00W0400--0000ROADS")
    map_number TEXT,                            -- MapNumber ("10S03W04")
    or_map_num TEXT,                            -- ORMapNum ("2210.00S03.00W0400--0000")
    record_name TEXT,                           -- RecordName (often = MapNumber)

    -- Acreage (three independent measures from source — preserved for ground-truthing)
    taxlot_acre DOUBLE PRECISION,               -- TaxlotAcre  (stated taxlot acreage)
    map_acres DOUBLE PRECISION,                 -- MapAcres    (map-derived acreage)
    calculated_acres DOUBLE PRECISION,          -- CalculatedAcres
    stated_area DOUBLE PRECISION,               -- StatedArea (with unit code)
    stated_area_unit INTEGER,                   -- StatedAreaUnit

    -- ORMAP audit / provenance (useful for delta-refresh + data-quality work)
    relia_code INTEGER,                         -- ReliaCode
    source TEXT,                                -- Source
    source_type TEXT,                           -- SourceType
    validation_status INTEGER,                  -- VALIDATIONSTATUS
    map_suf_type TEXT,                          -- MapSufType
    map_class TEXT,                             -- MapClass
    map_rel_code TEXT,                          -- MapRelCode
    map_suf_num INTEGER,                        -- MapSufNum
    is_seed INTEGER,                            -- IsSeed
    special_int TEXT,                           -- SpecialInt
    name TEXT,                                  -- Name
    misclose_ratio DOUBLE PRECISION,            -- MiscloseRatio
    misclose_distance DOUBLE PRECISION,         -- MiscloseDistance
    county_code INTEGER,                        -- County (ORMAP county code; 22 = Linn)

    -- Edit-tracking — drives the daily-delta refresh path
    created_user TEXT,
    created_date TIMESTAMPTZ,
    last_edited_user TEXT,
    last_edited_date TIMESTAMPTZ,
    auto_date TIMESTAMPTZ,
    auto_method TEXT,
    auto_who TEXT,
    global_id TEXT,

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_parcels_linn_geom ON parcels_linn USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_linn_taxlot ON parcels_linn(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_linn_map_taxlot ON parcels_linn(map_taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_linn_or_taxlot ON parcels_linn(or_taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_linn_map_number ON parcels_linn(map_number);
CREATE INDEX IF NOT EXISTS idx_parcels_linn_last_edited_date ON parcels_linn(last_edited_date);

COMMIT;
