-- parcels_jefferson: Jefferson County (FIPS 031) taxlot polygons + native attribute fields.
--
-- Source: gis.odf.oregon.gov/ags1/rest/services/WebMercator/TaxlotsDisplay/MapServer/15
--         (Oregon Department of Forestry statewide TaxlotsDisplay service, Jefferson
--         County layer 15. ODF aggregates county taxlot publications statewide; for
--         counties whose direct ArcGIS is Cloudflare-blocked from the droplet —
--         Jefferson's gis.jeffersoncountyor.gov returns 403 — the ODF overlay is
--         the reachable canonical source.)
-- Source SR: EPSG:2992 (Oregon Lambert ft NAD83/HARN). Server reprojects to 4326
--            on outSR=4326 + f=geojson, same pattern as Yamhill / Polk / Jackson.
-- Storage SR: 4326 (matches all other county tables).
-- Geometry: esriGeometryPolygon — ST_Multi() on write so geom is uniform
--            MultiPolygon(4326) across counties.
--
-- Estimated parcel count: 18,000–22,000 (Jefferson population ~25k, area ~1,791
-- sq mi, mostly ranch / dry-farm / forest east of the Deschutes River). Crook
-- was 17,532 at population ~24k — Jefferson should land in the same band.
--
-- Schema note vs siblings:
--   Jefferson's ODF Layer 15 ships the Yamhill-family schema in full — OWNERLINE1
--   + OWNERLINE2 + OWNERLINE3 + AGENTNAME for the multi-owner stack, full mailing
--   block (MAILADD1/2/CITY/STATE/ZIP/CNTRY), situs trio (SITEADDNAM/CTY/ZIP),
--   account family (PRIMACCNUM / SIMAPTAX / ORMapNum / ORTaxlot / MapNumber /
--   MapTaxlot / Taxlot / SpecialInt), the full ORMAP polygon attrs (MapSufType /
--   MapSufNum / MapClass / MapRelCode / ReliaCode), most-recent recording snapshot
--   (INSTYEAR + INSTMONTH + INSTID + INSTTYPE — INSTYEAR/INSTMONTH are doubles,
--   day synthesized as 01 per Yamhill pattern), property classification
--   (DWELLING + PRPCLASS + PRPCLSDSC), per-parcel REFLink, and full PLSS (County
--   / Town / TownPart / TownDir / Range / RangePart / RangeDir / SecNumber / Qtr
--   / QtrQtr / Anomaly).
--
--   Native acreage (TaxlotAcre polygon-native + TaxLotFeet conversion). Shape area
--   / length come back as Shape.STArea() / Shape.STLength() in the source — those
--   collapse to shape_area / shape_length via the geojson conversion. PK is
--   OBJECTID (not FID like Yamhill).
--
--   Per Yamhill's empty-but-present schema lesson (KB_87): keep PRPCLASS /
--   PRPCLSDSC / AGENTNAME / REFLink columns even if 0% populated on first ingest
--   so upstream backfills surface transparently when the assessor publishes them
--   downstream.
--
-- IN FLIGHT vs siblings (per the brief):
--   - Valuation: NO assessed-value or RMV fields in the ODF overlay (vs Polk
--     which ships AstImpVal/AstLndVal/AstValue, Douglas which ships full RMV +
--     assessed). The broker hits the assessor portal for valuation. Flag
--     honestly per DOCTRINE-HONEST-IN-FLIGHT-01.
--   - Building details (year built, sqft): not in source. Behind portal.
--   - Recorded sale price: not in source. Most-recent instrument id / type /
--     year / month are inline; price is IN FLIGHT.
--   - Zoning: no Jefferson zoning service yet located. PRPCLSDSC may carry
--     vineyard-equivalent ("FARM USE EFU" / "IRRIGATED") hints when populated —
--     probe at ingest; ship without conditional block on first cut.
--
-- Warm Springs Reservation exclusion: sovereign tribal land is typically NOT in
-- county taxlot publication. Expect not-in-coverage returns west of the Deschutes
-- River. This is correct behavior, not a bug — smoke #7 verifies.
--
-- county_fips column seeds the eventual unified `parcels` table migration.

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_jefferson (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,                    -- OBJECTID (source PK)
    county_fips TEXT NOT NULL DEFAULT '031',

    -- Taxlot identifiers
    taxlot TEXT NOT NULL,                       -- ORTaxlot (primary natural key, 29-char)
    maptaxlot TEXT,                             -- MapTaxlot (human-friendly variant)
    ortaxlot TEXT,                              -- ORTaxlot duplicate (kept for explicit join clarity)
    map_taxlot_short TEXT,                      -- Taxlot (5-char lot suffix within map)
    map_number TEXT,                            -- MapNumber
    ormapnum TEXT,                              -- ORMapNum (full 24-char ORMap)
    special_int TEXT,                           -- SpecialInt

    -- PLSS (inline)
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
    ref_link TEXT,                              -- REFLink

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
    inst_type TEXT,                             -- INSTTYPE

    -- Property classification
    dwelling TEXT,                              -- DWELLING (Y/N)
    prp_class TEXT,                             -- PRPCLASS
    prp_cls_desc TEXT,                          -- PRPCLSDSC

    -- Acreage / area
    taxlot_acres DOUBLE PRECISION,              -- TaxlotAcre (polygon native)
    taxlot_feet INTEGER,                        -- TaxLotFeet
    shape_area DOUBLE PRECISION,                -- Shape.STArea() (sq ft, source units)
    shape_length DOUBLE PRECISION,              -- Shape.STLength() (ft, source units)

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ                   -- editingInfo.dataLastEditDate (probe deeper-query per delta refresh)
);

CREATE INDEX IF NOT EXISTS idx_parcels_jefferson_geom ON parcels_jefferson USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_jefferson_taxlot ON parcels_jefferson(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_jefferson_maptaxlot ON parcels_jefferson(maptaxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_jefferson_map_number ON parcels_jefferson(map_number);
CREATE INDEX IF NOT EXISTS idx_parcels_jefferson_prim_acc_num ON parcels_jefferson(prim_acc_num);
CREATE INDEX IF NOT EXISTS idx_parcels_jefferson_owner_line1 ON parcels_jefferson(owner_line1);
CREATE INDEX IF NOT EXISTS idx_parcels_jefferson_site_add_cty ON parcels_jefferson(site_add_cty);
CREATE INDEX IF NOT EXISTS idx_parcels_jefferson_prp_class ON parcels_jefferson(prp_class);
CREATE INDEX IF NOT EXISTS idx_parcels_jefferson_prp_cls_desc ON parcels_jefferson(prp_cls_desc);

COMMIT;
