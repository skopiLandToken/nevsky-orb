-- parcels_columbia: Columbia County (FIPS 41-009) taxlot polygons + native assessor attrs.
--
-- Source: gis.columbiacountymaps.com/server/rest/services/TaxlotWb/FeatureServer/0
--         ("Taxlots", capabilities = Query,Extract — 28,806 lots, maxRecordCount 2000).
--
-- Why THIS source — the load-bearing decision (read before "fixing" this):
--   Columbia County's own GIS (gis.columbiacountyor.gov) does NOT resolve from the
--   droplet (NXDOMAIN), and columbiacountymaps.com is a Squarespace placeholder.
--   The county's PUBLIC WEB MAPS (gis.columbiacountymaps.com/ColumbiaCountyWebMaps)
--   went OFFLINE 2026-04-22 for an ADA/WCAG Title-II upgrade — BUT the underlying
--   ArcGIS Server REST endpoint (gis.columbiacountymaps.com/server/rest/services)
--   stayed live and queryable. Teaching note for Nevsky: a county taking its
--   human-facing web-map app offline does NOT necessarily take the REST server
--   down — probe /server/rest/services directly before assuming the county is dark.
--   And per the Jefferson lesson: always confirm `capabilities` includes "Query"
--   (TaxlotWb/0 advertises Query,Extract — bulk-exportable, unlike the ODF
--   TaxlotsDisplay trap which is Map-only).
--
-- Richness note vs siblings: Columbia is POLK/MARION-RICH. TaxlotWb/0 ships the
--   assessor account JOINED to polygon geometry in one row — OWNER, AGENT, full
--   mailing block (M_ADDRESS/M_CITY/M_STATE/ZIP), PRIMARY_SI (full situs string),
--   ACCOUNT_ID, property class (PROPERTY_C), residence/building counts (NUMHOUSES/
--   NUMBUILDIN), acreage (ACRES) + sqft (SQFT), and ACCELA_MT — the pre-formatted
--   Accela map-taxlot key (e.g. '7410-00-01001') that the state ePermitting portal
--   uses, stored so fetch_columbia_permits can deep-link straight into Accela.
--
-- OID note: the source's true OID field is OBJECTID_1 (the plain OBJECTID column
--   is published but 100% NULL). objectid below stores OBJECTID_1; pagination and
--   ON CONFLICT key off it. A prior cut that keyed on OBJECTID would have collided
--   every row to NULL — verified 2026-05-30.
--
-- Situs parse: PRIMARY_SI is a single combined string "{street} {CITY} OR {zip}".
--   primary_situs keeps it verbatim (always display-correct). site_add_nam/
--   site_add_cty/site_zip are best-effort split via a KNOWN-CITY suffix match
--   against Columbia's incorporated cities + CDPs (the only reliable split when
--   street and city share a space-delimited field). When the suffix doesn't match
--   a known place, city/street stay NULL and primary_situs carries the full line —
--   honest IN FLIGHT, no fabricated splits.
--
-- IN FLIGHT vs siblings (per DOCTRINE-HONEST-IN-FLIGHT-01):
--   - Valuation: NO assessed or RMV values in the public REST layer (vs Polk's
--     assessed-only, Douglas's full RMV). Lives in the bi-weekly Tax26.mdb export
--     + the assessor portal; not in TaxlotWb/0. Caller surfaces null valuation.
--   - Building details (year built / bed / bath): not in source. SQFT + dwelling
--     counts are present; finer detail is behind the portal.
--   - Recorded deeds / sale chain: not inline. The county publishes a separate
--     `Sales` FeatureServer (Good Sales 1/2/3-yrs-ago, queryable) — queued as a
--     fetch_columbia_records L3 follow-up; v1 records is L1 owner-of-record +
--     clerk deep link.
--   - Zoning: Columbia Land_Development folder not yet probed for a zoning layer;
--     queued behind the L1 ship.
--
-- Storage SR: 4326 (matches all other county tables). Source reprojects its native
--   SR to 4326 on outSR=4326 + f=geojson. Geometry: esriGeometryPolygon — ST_Multi
--   on write so geom is uniform MultiPolygon(4326) across counties.
--
-- county_fips column seeds the eventual unified `parcels` table migration.

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_columbia (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,                     -- OBJECTID_1 (true source OID; OBJECTID is NULL)
    county_fips TEXT NOT NULL DEFAULT '009',

    -- Taxlot identifiers
    taxlot TEXT NOT NULL,                        -- MAP_TAX (primary natural key, e.g. '7N4W1000 1001')
    maptaxlot TEXT,                              -- MAP_TAX (mirror, human-friendly)
    map_number TEXT,                             -- MAPNUM
    accela_mt TEXT,                              -- ACCELA_MT (Accela map-taxlot key, e.g. '7410-00-01001')
    account_id INTEGER,                          -- ACCOUNT_ID (assessor account number)

    -- Ownership / mailing
    owner_line1 TEXT,                            -- OWNER
    agent_name TEXT,                             -- AGENT
    mail_add1 TEXT,                              -- M_ADDRESS
    mail_city TEXT,                              -- M_CITY
    mail_state TEXT,                             -- M_STATE
    mail_zip TEXT,                               -- ZIP

    -- Situs (parsed from PRIMARY_SI; primary_situs holds the verbatim combined line)
    primary_situs TEXT,                          -- PRIMARY_SI (full "street CITY OR zip", verbatim)
    site_add_nam TEXT,                           -- parsed street portion (NULL if no known-city split)
    site_add_cty TEXT,                           -- parsed city (known-city suffix match; NULL otherwise)
    site_zip TEXT,                               -- parsed trailing 5-digit zip

    -- Property classification / structures
    prp_class TEXT,                              -- PROPERTY_C (Oregon property class code)
    num_houses INTEGER,                          -- NUMHOUSES (count of residences)
    num_buildings INTEGER,                       -- NUMBUILDIN (count of buildings)
    dwelling TEXT,                               -- derived 'Y'/'N' from num_houses

    -- Acreage / area
    taxlot_acres DOUBLE PRECISION,               -- ACRES (assessor of record)
    taxlot_sqft INTEGER,                         -- SQFT
    size_label TEXT,                             -- SIZE (display string, e.g. '63.63 ac' / '6945 sf')
    shape_area DOUBLE PRECISION,                 -- Shape__Area (source units)
    shape_length DOUBLE PRECISION,               -- Shape__Length (source units)

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,                        -- full source props verbatim (IMAGE/IMAGE2/MA/SA/CODE/etc.)
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_parcels_columbia_geom ON parcels_columbia USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_columbia_taxlot ON parcels_columbia(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_columbia_maptaxlot ON parcels_columbia(maptaxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_columbia_map_number ON parcels_columbia(map_number);
CREATE INDEX IF NOT EXISTS idx_parcels_columbia_accela_mt ON parcels_columbia(accela_mt);
CREATE INDEX IF NOT EXISTS idx_parcels_columbia_account_id ON parcels_columbia(account_id);
CREATE INDEX IF NOT EXISTS idx_parcels_columbia_owner_line1 ON parcels_columbia(owner_line1);
CREATE INDEX IF NOT EXISTS idx_parcels_columbia_site_add_cty ON parcels_columbia(site_add_cty);
CREATE INDEX IF NOT EXISTS idx_parcels_columbia_prp_class ON parcels_columbia(prp_class);

COMMIT;
