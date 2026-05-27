-- parcels_crook: Crook County (FIPS 013) taxlot polygons + native attribute fields.
--
-- Source: gis.crookcountyor.gov CC_Taxlots_Helion FeatureServer layer 0 (Helion = Crook's records system).
-- Source SR: EPSG:2914 (Oregon North NAD83 HARN ftUS); we pull with outSR=4326 so server reprojects.
-- Storage SR: 4326 (matches parcels_deschutes for cross-county spatial unions).
--
-- Schema note vs parcels_deschutes:
--   Crook ships richer attribute data inline — owner_name, situs_address, zone_code+zone_desc, multiple
--   assessor URLs — so this table absorbs what Deschutes had to lazy-fetch via DIAL. Layer-2 fetchers
--   for Crook only need to extend with sales history + valuation + tax history.
--
-- county_fips column is the seed for the eventual unified `parcels` table migration
-- (queued post-3-county-expansion; not done now to avoid regressing yesterday's TERRA Yield Calc on Deschutes).

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_crook (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    taxlot TEXT NOT NULL,
    county_fips TEXT NOT NULL DEFAULT '013',

    -- Native ownership + address (would be a DIAL fetch on Deschutes)
    owner_name TEXT,
    situs_address TEXT,
    mailing_address TEXT,

    -- Account / class
    account INTEGER,
    prop_class TEXT,
    pc_desc TEXT,
    tax_code_area TEXT,

    -- Subdivision detail
    sub_name TEXT,
    sub_block TEXT,
    sub_lot TEXT,

    -- Acreage (two sources — keep both)
    assessed_acres DOUBLE PRECISION,
    gis_acres DOUBLE PRECISION,

    -- Zoning is native (no PostGIS polygon join needed for Crook)
    zone_code TEXT,
    zone_desc TEXT,

    -- Tax map + assessor URLs
    map_number TEXT,
    pats_url TEXT,
    tax_card_url TEXT,
    tax_map_url TEXT,
    pso_url TEXT,
    global_id TEXT,

    -- Shape metrics + geometry
    shape_length DOUBLE PRECISION,
    shape_area DOUBLE PRECISION,
    geom GEOMETRY(MultiPolygon, 4326),

    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_parcels_crook_geom ON parcels_crook USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_crook_taxlot ON parcels_crook(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_crook_zone_code ON parcels_crook(zone_code);
CREATE INDEX IF NOT EXISTS idx_parcels_crook_owner_name ON parcels_crook(owner_name);
CREATE INDEX IF NOT EXISTS idx_parcels_crook_account ON parcels_crook(account);

COMMIT;
