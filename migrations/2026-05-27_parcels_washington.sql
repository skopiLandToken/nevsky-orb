-- parcels_washington: Washington County (FIPS 067) taxlot polygons.
--
-- Source: gispub.co.washington.or.us ArcGIS server,
--         AT_Cartog/Washington_County_Addresses_and_Taxlots/MapServer/1 (Taxlots layer).
-- Source spatial reference: WKID 2913 (Oregon Lambert / NAD83 HARN).
-- Storage SR: 4326 (matches parcels_deschutes + parcels_crook + parcels_multnomah for
--             cross-county spatial unions). outSR=4326 on query reprojects server-side.
-- Source geometry: esriGeometryPolygon (single Polygon).
-- Cast: ST_Multi() during ingest so geom type is MultiPolygon(4326) uniformly.
--
-- recordCount at ingest authoring time (2026-05-27): 200,345 parcels.
--
-- Schema reality vs Multnomah:
--   Washington County's public taxlot service is the OPPOSITE of Multnomah — it ships
--   only TLNO + MAPNO + TLNO5 + geometry. There is NO inline owner / situs / valuation /
--   sale / zoning. The rich attribute set lives behind the A&T portal
--   (washcotax.co.washington.or.us / DotNetNuke + ASP.NET WebForms with viewstate gating).
--   Layer 2 fetchers will pull what they can on demand + flag the rest IN FLIGHT
--   pending viewstate-capture or paid-tier authorization.
--
--   Zoning is in a separate layer (Intermap/Land_Use/MapServer/8 — "County Land Use
--   Districts (Zoning)") and is pulled live via the L2 fetcher rather than baked into
--   parcels_washington — keeps L1 ingest single-source-of-truth and avoids stale joins.
--
-- county_fips column is the seed for the eventual unified `parcels` table migration
-- (queued post-3-county-expansion per parcels_crook migration note).

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_washington (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    taxlot TEXT NOT NULL,                      -- TLNO (12-char)
    county_fips TEXT NOT NULL DEFAULT '067',

    -- Native identifiers
    map_number TEXT,                           -- MAPNO (7-char map sheet ID, e.g. '1N31500')
    taxlot_short INTEGER,                      -- TLNO5 (last 5 digits as integer; not unique alone)

    -- Shape metrics + geometry
    shape_length DOUBLE PRECISION,             -- SHAPE.STLength() (esriFeet)
    shape_area DOUBLE PRECISION,               -- SHAPE.STArea()   (esriFeet²)
    geom GEOMETRY(MultiPolygon, 4326),

    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_parcels_washington_geom ON parcels_washington USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_washington_taxlot ON parcels_washington(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_washington_map_number ON parcels_washington(map_number);

COMMIT;
