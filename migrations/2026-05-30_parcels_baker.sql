-- parcels_baker: Baker County (FIPS 41-001) taxlot polygons — THIN cadastral.
--
-- Source: gis.odot.state.or.us/arcgis1006/rest/services/ames/ames/MapServer/28
--         ("Baker County Taxlots", capabilities=Map,Query,Data, maxRecordCount 5000,
--          16,671 lots). ODOT's statewide "ames" cadastral service.
--
-- Why THIS source — the load-bearing decision (read before "fixing" this):
--   Baker has NO queryable county-direct ArcGIS REST endpoint (bakercountyor.gov/map
--   is static HTML; gis.bakercounty.org serves nothing on any ArcGIS path), and the
--   ODF statewide TaxlotsDisplay is Map-only (no Query — the Jefferson-lesson trap).
--   The ODOT "ames" MapServer is the reachable queryable statewide cadastral: it
--   hosts per-county taxlot layers (Baker=28) with FULL Query,Data capability and
--   geojson+4326 reprojection. Teaching note for Nevsky: when the county is dark and
--   ODF is display-only, ODOT's ames service is the third statewide fallback — it
--   carries 13 counties' taxlots (Baker/Curry/Grant/Harney/Klamath/Lake/Wheeler are
--   ONLY reachable this way) and is fully queryable, unlike ODF.
--
-- THIN by nature — honest per DOCTRINE-HONEST-IN-FLIGHT-01:
--   ODOT maintains this cadastral for road / right-of-way mapping, so it ships ONLY
--   geometry + taxlot identity: County, MapNumber, Taxlot, MapTaxlot, EFFECTV_DT.
--   There is NO owner, NO situs, NO acreage field, NO valuation, NO property class —
--   and for a county Baker's size that assessor data is NOT in any free public layer
--   (it lives behind the Baker County Assessor Property Search, one parcel at a time,
--   or is sold by commercial aggregators). A downloadable shapefile would be NO
--   richer. So owner/situs are present-but-NULL columns here (forward-compat + the
--   coordinate-resolver expects them); acreage is COMPUTED from the polygon at read
--   time via ST_Area(geom::geography) — measured from the official cadastral, not
--   fabricated. This thin schema is the reusable template for the other ODOT-ames
--   counties (Curry/Grant/Harney/Klamath/Lake/Wheeler).
--
-- Storage SR: 4326 (matches all other county tables). Source reprojects native SR to
--   4326 on outSR=4326 + f=geojson. Geometry: esriGeometryPolygon — ST_Multi on write.
--
-- county_fips column seeds the eventual unified `parcels` table migration. (ODOT's
--   own "County" code — Baker=1 — is preserved in raw_properties; county_fips here is
--   the real FIPS '001'.)

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_baker (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,                     -- OBJECTID (source PK)
    county_fips TEXT NOT NULL DEFAULT '001',

    -- Taxlot identifiers (the only real attributes ODOT ames carries)
    taxlot TEXT NOT NULL,                        -- MapTaxlot (natural key, e.g. '06S38E00300')
    maptaxlot TEXT,                              -- MapTaxlot (mirror)
    map_taxlot_short TEXT,                        -- Taxlot (lot suffix, e.g. '300')
    map_number TEXT,                             -- MapNumber (e.g. '06S38E')
    effective_year TEXT,                         -- EFFECTV_DT (cadastral effective year)

    -- Assessor attrs NOT in the ODOT cadastral source. Present-but-NULL for
    -- forward-compat + coordinate-resolver uniformity; backfill if a richer Baker
    -- source ever lands. NEVER fabricate values into these.
    owner_line1 TEXT,
    site_add_nam TEXT,
    site_add_cty TEXT,

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_parcels_baker_geom ON parcels_baker USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_baker_taxlot ON parcels_baker(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_baker_maptaxlot ON parcels_baker(maptaxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_baker_map_number ON parcels_baker(map_number);

COMMIT;
