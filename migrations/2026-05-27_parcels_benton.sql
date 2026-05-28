-- parcels_benton: Benton County (FIPS 003) taxlot polygons + owner-exploded attribute rows.
--
-- Source: gis.co.benton.or.us/arcgis/rest/services/Public/TaxlotOwners/FeatureServer/0
--         (Benton County ArcGIS Server 10.71, public folder, "Tax Lots with Account
--          Information" service. Read-only Query capability.)
-- Source SR: EPSG:2913 (Oregon State Plane North NAD83 HARN, feet). Server reprojects
--            on outSR=4326 / f=geojson.
-- Storage SR: 4326 (matches sibling parcels_* tables).
-- Geometry: esriGeometryPolygon (single). Cast ST_Multi() on write so geom is
--           MultiPolygon(4326) uniformly across the county set.
--
-- recordCount target at ingest authoring time (2026-05-27): 107,988 rows.
-- IMPORTANT: this is OWNER-EXPLODED. Same parcel polygon repeats once per
--            Party_Name (e.g., trust co-trustees yield 3 rows w/ identical
--            geom+Account_Num+ORTaxlot). Estimated unique parcels: ~36-40k.
--            Dedup happens at query time via STRING_AGG over Party_Name in the
--            COUNTY_REGISTRY normalized lookup (orb_db.py), not at ingest.
--            Ingesting all rows preserves owner fidelity for L2 fetchers.
--
-- Schema note vs siblings:
--   Benton TaxlotOwners ships situs + owner + mailing inline but does NOT ship
--   zoning, acres, valuation, or year_built. Those resolve via L2 fetchers
--   (Benton Zoning service for zone_code; assessment portal for valuation).
--   Closer to Washington's pattern than Marion's.
--
-- county_fips column seeds the eventual unified `parcels` table migration.

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_benton (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    county_fips TEXT NOT NULL DEFAULT '003',

    -- Account / canonical taxlot identifiers
    account_num TEXT,                          -- Account_Num (6-char A&T account)
    maptaxlot TEXT,                            -- MapTaxlot (12-char compact)
    ortaxlot TEXT NOT NULL,                    -- ORTaxlot (29-char canonical OR format)
    tax_code_area TEXT,                        -- Tax_Code_Area

    -- Situs (parcel street address)
    situs_addr1 TEXT,                          -- Situs_Addr1 ("UNASSIGNED" for vacant)
    situs_city TEXT,                           -- Situs_City
    situs_state TEXT,                          -- Situs_State
    situs_zip TEXT,                            -- Situs_Zip

    -- Ownership (owner-exploded — one row per Party_Name)
    party_name TEXT,                           -- Party_Name (40 char)
    in_care_of TEXT,                           -- In_Care_Of

    -- Mailing address (where roll notices go)
    mail_line1 TEXT,                           -- Mail_Line1
    mail_line2 TEXT,                           -- Mail_Line2
    mail_city TEXT,                            -- Mail_City
    mail_state TEXT,                           -- Mail_State
    mail_zip TEXT,                             -- Mail_Zip

    -- Map number (taxmap sheet reference)
    map_number TEXT,                           -- MapNumber

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- No last_modified: TaxlotOwners doesn't expose a LASTUPDATE field.
    -- Daily delta will require full re-ingest or OBJECTID-range scan.
);

CREATE INDEX IF NOT EXISTS idx_parcels_benton_geom ON parcels_benton USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_benton_ortaxlot ON parcels_benton(ortaxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_benton_maptaxlot ON parcels_benton(maptaxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_benton_account ON parcels_benton(account_num);
CREATE INDEX IF NOT EXISTS idx_parcels_benton_party_name ON parcels_benton(party_name);
CREATE INDEX IF NOT EXISTS idx_parcels_benton_situs_city ON parcels_benton(situs_city);

COMMIT;
