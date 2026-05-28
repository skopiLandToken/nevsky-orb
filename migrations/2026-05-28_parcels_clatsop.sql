-- parcels_clatsop: Clatsop County (FIPS 41-007) taxlot polygons + native attribute fields.
--
-- Source: https://delta.co.clatsop.or.us/server/rest/services/Taxlots/FeatureServer/1
--         (Clatsop County's own ArcGIS Server — note the instance is `/server`, NOT
--         `/arcgis`; the root REST dir is public, the Assessment_Tax folder is
--         token-gated. The county-direct Taxlots layer is the canonical L1 source —
--         the ODF statewide TaxlotsDisplay MapServer has query DISABLED (capability
--         "Map" only), so the county-direct FeatureServer is what actually serves
--         features for the coastal Tier 3 cluster.)
-- Source SR: server reprojects to 4326 on outSR=4326 + f=geojson (Shape__Area stays
--            in the source projected sqft). Storage SR: 4326 (matches all sibling tables).
-- Geometry: esriGeometryPolygon — ST_Multi() on write so geom is uniform
--           MultiPolygon(4326) across counties.
--
-- Parcel count at discovery: 35,456 (returnCountOnly). maxRecordCount = 5000.
--
-- Schema note vs siblings:
--   Clatsop ships its OWN schema, NOT the ODF/Yamhill family. It carries a RICHER
--   inline surface than the thin ODF cadastre on the ownership side — OWNER_LINE +
--   OWNER_LL_1 + OWNER_LL_2 (up to three owner lines) + IN_CARE_OF, full mailing
--   block (STREET_ADD / OPTIONAL_A / PO_BOX / UNIT_NUMBE / CITY / STATE / ZIP_CODE),
--   situs (SITUS_ADDR + SITUS_CITY), account (ACCOUNT_ID integer), property class
--   (PROPERTY_C + STAT_CLASS), YEAR_BUILT, tax code (TAXCODE), maintenance-area /
--   neighborhood (MA / NH), septic status, and the full taxmap key family
--   (TAXLOTKEY / TAXMAPKEY / TAXMAPNUM / MAPNUM / SIMapTax / Taxlot / ORTaxlot).
--
--   So fetch_clatsop_assessment + fetch_clatsop_records are BOTH SQL pass-throughs
--   over L1 inline (Josephine / Yamhill pattern) — no scraper, no viewstate gating,
--   no external HTTP per call.
--
--   No native acreage column — acres derive geodesically from geom
--   (ST_Area(geom::geography) / 4046.8564224) in the COUNTY_REGISTRY query, same
--   fallback Yamhill / Douglas use.
--
--   Per the empty-but-present-column lesson (KB_87): keep every source column even
--   if sparsely populated so upstream backfills surface transparently.
--
-- IN FLIGHT vs siblings (flag honestly per DOCTRINE-HONEST-IN-FLIGHT-01):
--   - Valuation: NO assessed-value or RMV fields in the public Taxlots layer.
--     Valuation lives in the token-gated Assessment_Tax folder (no public access).
--     The broker hits the assessor surface for valuation.
--   - Recorded sale / instrument: NOT exposed by this layer (no INSTID/INSTYEAR).
--     Sale chain is behind the Clerk's recording surface.
--   - Zoning: NOT on the taxlot record — it lives on a SEPARATE queryable service
--     (Zoning_Layers/MapServer, layers 0-5: county + Astoria / Cannon Beach /
--     Gearhart / Seaside / Warrenton). Zoning ingest queued (Jackson pattern);
--     not blocking the L1+L2 ship.
--
-- county_fips column seeds the eventual unified `parcels` table migration.

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_clatsop (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,                     -- OBJECTID (source PK)
    county_fips TEXT NOT NULL DEFAULT '007',

    -- Taxlot identifiers
    taxlot TEXT NOT NULL,                        -- ORTaxlot (primary natural key, 29-char)
    ortaxlot TEXT,                               -- ORTaxlot duplicate (explicit join clarity)
    taxlot_short TEXT,                           -- Taxlot (5-char lot suffix within map)
    taxmapnum TEXT,                              -- TAXMAPNUM
    taxmapkey TEXT,                              -- TAXMAPKEY
    taxlotkey TEXT,                              -- TAXLOTKEY
    map_number TEXT,                             -- MAPNUM
    si_map_tax TEXT,                             -- SIMapTax

    -- Account
    account_id INTEGER,                          -- ACCOUNT_ID

    -- Ownership
    owner_line TEXT,                             -- OWNER_LINE (primary)
    owner_ll_1 TEXT,                             -- OWNER_LL_1 (co-owner / trustee 2)
    owner_ll_2 TEXT,                             -- OWNER_LL_2 (co-owner / trustee 3)
    in_care_of TEXT,                             -- IN_CARE_OF

    -- Mailing
    street_add TEXT,                             -- STREET_ADD
    optional_a TEXT,                             -- OPTIONAL_A (mailing line 2)
    po_box TEXT,                                 -- PO_BOX
    unit_number TEXT,                            -- UNIT_NUMBE
    mail_city TEXT,                              -- CITY
    mail_state TEXT,                             -- STATE
    mail_zip TEXT,                               -- ZIP_CODE

    -- Situs
    situs_address TEXT,                          -- SITUS_ADDR
    situs_city TEXT,                             -- SITUS_CITY

    -- Classification / building
    property_class TEXT,                         -- PROPERTY_C
    stat_class TEXT,                             -- STAT_CLASS
    year_built SMALLINT,                         -- YEAR_BUILT (double in source)
    tax_code TEXT,                               -- TAXCODE
    ma TEXT,                                     -- MA (maintenance area)
    nh TEXT,                                     -- NH (neighborhood)
    septic_status SMALLINT,                      -- SEPTIC_STATUS
    own_sit_lot TEXT,                            -- OwnSitLot
    labeling TEXT,                               -- Labeling

    -- Area
    shape_area DOUBLE PRECISION,                 -- Shape__Area (sq ft, source units)

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_parcels_clatsop_geom ON parcels_clatsop USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_clatsop_taxlot ON parcels_clatsop(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_clatsop_ortaxlot ON parcels_clatsop(ortaxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_clatsop_taxmapnum ON parcels_clatsop(taxmapnum);
CREATE INDEX IF NOT EXISTS idx_parcels_clatsop_account_id ON parcels_clatsop(account_id);
CREATE INDEX IF NOT EXISTS idx_parcels_clatsop_owner_line ON parcels_clatsop(owner_line);
CREATE INDEX IF NOT EXISTS idx_parcels_clatsop_situs_city ON parcels_clatsop(situs_city);
CREATE INDEX IF NOT EXISTS idx_parcels_clatsop_property_class ON parcels_clatsop(property_class);

COMMIT;
