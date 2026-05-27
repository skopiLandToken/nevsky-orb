-- parcels_clackamas: Clackamas County (FIPS 005) taxlot polygons.
--
-- Source: Clackamas County GIS — Taxlots hosted feature layer view at
--         services3.arcgis.com/I2eWXOndpF9m8oKC/arcgis/rest/services/Taxlots/FeatureServer/0
-- Source spatial reference: WKID 102100 / 3857 (Web Mercator).
-- Storage SR: 4326 — matches every other parcels_* table so cross-county
--             spatial unions work without per-row reprojection. outSR=4326
--             on query reprojects server-side.
-- Source geometry: esriGeometryPolygon (single Polygon).
-- Cast: ST_Multi() during ingest so geom type is MultiPolygon(4326) uniformly.
--
-- recordCount at ingest authoring time (2026-05-27): 163,585 parcels.
-- Refresh cadence at source: weekly (per HFLV description).
--
-- Schema reality:
--   Clackamas's hosted Taxlots view ships ONLY TLNO + PARCEL_NUMBER + SITUS +
--   SITUS_CITY + SITUS_ZIP + TAXCODE + geometry. NO inline owner / valuation /
--   sale / zoning — explicitly stripped from the public view per the source
--   description ("No tax payer information is available within this View").
--   Rich attributes live behind the A&T portal at ascendweb.clackamas.us,
--   which is ASP.NET WebForms with __VIEWSTATE gating (same blocker as
--   washcotax.co.washington.or.us and multcoproptax). Layer 2 fetchers pull
--   what they can on demand + flag the rest IN FLIGHT.
--
-- Shape__Area / Shape__Length at source are in esriMeters (Web Mercator,
--   distorted by latitude). For accurate acres we use ST_Area(geom::geography)
--   at query time, NOT the stored shape_area attribute.
--
-- county_fips column is the seed for the eventual unified `parcels` table
-- migration (queued post-3-county-expansion per parcels_crook migration note).

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_clackamas (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    taxlot TEXT NOT NULL,                      -- TLNO (20-char)
    county_fips TEXT NOT NULL DEFAULT '005',

    -- Native identifiers
    parcel_number TEXT,                        -- PARCEL_NUMBER (8-char account / map account)
    tax_code TEXT,                             -- TAXCODE (6-char tax code area)

    -- Situs
    situs_address TEXT,                        -- SITUS (40-char street address)
    situs_city TEXT,                           -- SITUS_CITY (15-char)
    situs_zip INTEGER,                         -- SITUS_ZIP

    -- Shape metrics + geometry
    -- shape_area/shape_length stored as-reported (Web Mercator m²/m, distorted by latitude).
    -- For accurate acres use ST_Area(geom::geography) / 4046.8564224 — see COUNTY_REGISTRY.
    shape_length DOUBLE PRECISION,
    shape_area DOUBLE PRECISION,
    geom GEOMETRY(MultiPolygon, 4326),

    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_parcels_clackamas_geom ON parcels_clackamas USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_clackamas_taxlot ON parcels_clackamas(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_clackamas_parcel_number ON parcels_clackamas(parcel_number);
CREATE INDEX IF NOT EXISTS idx_parcels_clackamas_situs_city ON parcels_clackamas(situs_city);

COMMIT;
