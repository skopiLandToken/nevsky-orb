-- parcels_yamhill: Yamhill County (FIPS 071) taxlot polygons + native attribute fields.
--
-- Source: services.arcgis.com/uUvqNMGPm7axC2dD/ArcGIS/rest/services/Yamhill_County_Landowners/FeatureServer/0
--         (Statewide ArcGIS Online org hosting Yamhill 2025 landowners layer.)
-- Source SR: EPSG:2992 (Oregon Lambert ft NAD83/HARN). Server reprojects to 4326
--            on outSR=4326 + f=geojson, same pattern as Polk/Jackson.
-- Storage SR: 4326 (matches all other county tables).
-- Geometry: esriGeometryPolygon — ST_Multi() on write so geom is uniform
--            MultiPolygon(4326) across counties.
--
-- recordCount at ingest authoring time (2026-05-28): 42,852 parcels.
--
-- Schema note vs siblings:
--   Yamhill's Landowners service ships the ORMAP polygon attrs + assessor account
--   attrs already joined in one row, like Polk. Owner data is richer — OWNERLINE1,
--   OWNERLINE2, OWNERLINE3, AGENTNAME (up to four owner strings) so trusts /
--   multi-owner parcels surface cleanly without an owner-explosion table (Benton's
--   problem) or a separate Assessor scrape (Linn's blocker).
--
--   Property classification fields (PRPCLASS + PRPCLSDSC) are the vineyard demo
--   gold — EFU / AF / FOREST / FARMSITE designations are inline. The wine-country
--   broker pitch lands here without a separate zoning service.
--
--   Per-parcel deep link inline as REFLink — first county where the assessor
--   exposes a parcel-level canonical URL in the bulk service. Capture verbatim.
--
--   Full PLSS inline (County / Town / TownPart / TownDir / Range / RangePart /
--   RangeDir / SecNumber / Qtr / QtrQtr) — useful for section_id composition and
--   future PLSS-based queries (timber tracts, easement crosswalks).
--
--   Native last-recording snapshot (INSTYEAR / INSTMONTH / INSTID / INSTTYPE) so
--   fetch_yamhill_records returns the most-recent recording inline without a
--   Clerk-portal scrape.
--
-- IN FLIGHT vs siblings:
--   - Valuation: NO assessed-value or RMV fields in the source (vs Polk which
--     ships AstImpVal/AstLndVal/AstValue). The broker hits the assessor portal
--     via REFLink for valuation. Flag honestly per DOCTRINE-HONEST-IN-FLIGHT-01.
--   - Zoning code separate from PRPCLSDSC: Yamhill does NOT publish a
--     standalone ZoningDistricts FS in the discoverable org. PRPCLSDSC carries
--     the property-class flavor (EFU / AF / FARMSITE / FOREST) which is the
--     differentiator for vineyard parcels; a future Yamhill-direct zoning layer
--     would be additive.
--   - Building details (year built, sqft): not in source. Behind portal.
--
-- county_fips column seeds the eventual unified `parcels` table migration.

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_yamhill (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,                    -- FID (source PK)
    county_fips TEXT NOT NULL DEFAULT '071',

    -- Taxlot identifiers
    taxlot TEXT NOT NULL,                       -- ORTaxlot (primary natural key, 29-char)
    maptaxlot TEXT,                             -- MapTaxlot (human-friendly variant)
    ortaxlot TEXT,                              -- ORTaxlot duplicate (kept for explicit join clarity)
    map_taxlot_short TEXT,                      -- Taxlot (5-char lot suffix within map)
    map_number TEXT,                            -- MapNumber (7.5.29 style)
    ormapnum TEXT,                              -- ORMapNum (full 24-char ORMap)
    special_int TEXT,                           -- SpecialInt

    -- PLSS (inline in Yamhill source — bonus over siblings)
    plss_county SMALLINT,                       -- County
    plss_town SMALLINT,                         -- Town
    plss_town_part DOUBLE PRECISION,            -- TownPart
    plss_town_dir TEXT,                         -- TownDir
    plss_range SMALLINT,                        -- Range
    plss_range_part DOUBLE PRECISION,           -- RangePart
    plss_range_dir TEXT,                        -- RangeDir
    plss_section SMALLINT,                      -- SecNumber
    plss_qtr TEXT,                              -- Qtr
    plss_qtr_qtr TEXT,                          -- QtrQtr
    plss_anomaly TEXT,                          -- Anomaly

    -- Map suffix / class metadata
    map_suf_type TEXT,                          -- MapSufType
    map_suf_num INTEGER,                        -- MapSufNum
    map_class TEXT,                             -- MapClass
    map_rel_code TEXT,                          -- MapRelCode
    relia_code INTEGER,                         -- ReliaCode

    -- Account / per-parcel deep link
    prim_acc_num TEXT,                          -- PRIMACCNUM
    si_map_tax TEXT,                            -- SIMAPTAX
    ref_link TEXT,                              -- REFLink — per-parcel canonical URL (Yamhill unique)

    -- Ownership / mailing
    owner_line1 TEXT,                           -- OWNERLINE1 (primary fee owner)
    owner_line2 TEXT,                           -- OWNERLINE2 (co-owner / trustee 2)
    owner_line3 TEXT,                           -- OWNERLINE3 (co-owner / trustee 3)
    agent_name TEXT,                            -- AGENTNAME (display field)
    mail_add1 TEXT,                             -- MAILADD1
    mail_add2 TEXT,                             -- MAILADD2
    mail_city TEXT,                             -- MAILCITY
    mail_state TEXT,                            -- MAILSTATE
    mail_zip TEXT,                              -- MAILZIP
    mail_country TEXT,                          -- MAILCNTRY

    -- Situs
    site_add_nam TEXT,                          -- SITEADDNAM
    site_add_cty TEXT,                          -- SITEADDCTY
    site_zip TEXT,                              -- SITEZIP

    -- Last instrument (deed/transfer snapshot — inline in source)
    inst_year SMALLINT,                         -- INSTYEAR
    inst_month SMALLINT,                        -- INSTMONTH
    inst_id TEXT,                               -- INSTID
    inst_type TEXT,                             -- INSTTYPE (WARRANTY DEED, etc)

    -- Property classification (vineyard demo gold)
    dwelling TEXT,                              -- DWELLING (Y/N)
    prp_class TEXT,                             -- PRPCLASS
    prp_cls_desc TEXT,                          -- PRPCLSDSC (EFU / AF / FARMSITE / FOREST shows here)

    -- Acreage / area
    taxlot_acres DOUBLE PRECISION,              -- TaxlotAcre (polygon native)
    taxlot_feet INTEGER,                        -- TaxLotFeet
    shape_area DOUBLE PRECISION,                -- Shape__Area in source units (sq ft)
    shape_length DOUBLE PRECISION,              -- Shape__Length in source units (ft)

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ                   -- editingInfo.dataLastEditDate captured per delta refresh
);

CREATE INDEX IF NOT EXISTS idx_parcels_yamhill_geom ON parcels_yamhill USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_yamhill_taxlot ON parcels_yamhill(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_yamhill_maptaxlot ON parcels_yamhill(maptaxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_yamhill_map_number ON parcels_yamhill(map_number);
CREATE INDEX IF NOT EXISTS idx_parcels_yamhill_prim_acc_num ON parcels_yamhill(prim_acc_num);
CREATE INDEX IF NOT EXISTS idx_parcels_yamhill_owner_line1 ON parcels_yamhill(owner_line1);
CREATE INDEX IF NOT EXISTS idx_parcels_yamhill_site_add_cty ON parcels_yamhill(site_add_cty);
CREATE INDEX IF NOT EXISTS idx_parcels_yamhill_prp_class ON parcels_yamhill(prp_class);
CREATE INDEX IF NOT EXISTS idx_parcels_yamhill_prp_cls_desc ON parcels_yamhill(prp_cls_desc);

COMMIT;
