-- parcels_tillamook: Tillamook County (FIPS 41-057) taxlot polygons + native attribute fields.
--
-- Source: https://services.arcgis.com/uUvqNMGPm7axC2dD/arcgis/rest/services/
--         Tilllamook_Taxlot_Owners/FeatureServer/0
--         (Hosted ArcGIS Online feature service in the State-of-Oregon-affiliated
--         AGO org uUvqNMGPm7axC2dD — the SAME org that hosts Yamhill_County_Landowners.
--         NOTE the triple-L typo "Tilllamook" is in the actual service name — do
--         NOT correct it. The ODF statewide TaxlotsDisplay MapServer has query
--         DISABLED, and the county-domain GIS hosts (gis.co.tillamook.or.us etc.)
--         have no DNS / 403 from the droplet, so this hosted AGO layer is the
--         canonical reachable L1 source.)
-- Source SR: server reprojects to 4326 on outSR=4326 + f=geojson. Storage SR: 4326.
-- Geometry: esriGeometryPolygon — ST_Multi(ST_CollectionExtract(ST_MakeValid(...),3))
--           on write so geom is uniform valid MultiPolygon(4326) across counties.
--
-- Parcel count at discovery: 31,329 (returnCountOnly). maxRecordCount = 2000.
-- Source PK is FID (not OBJECTID like the ODF overlay, not OID-with-OBJECTID like
-- the county-direct servers). FID 1 is a "ROADS" placeholder row (blank owner) —
-- kept for consistency with Yamhill (siblings ingest ROADS rows; a pin-drop on a
-- road right-of-way correctly returns the ROADS taxlot).
--
-- Schema note vs siblings:
--   Tillamook ships the FULL Yamhill/ODF family schema (OWNERLINE1/2/3 + AGENTNAME,
--   full mailing block, situs trio, account family, ORMAP polygon attrs, PLSS,
--   most-recent recording snapshot INSTYEAR/INSTMONTH/INSTID/INSTTYPE, property
--   class DWELLING/PRPCLASS/PRPCLSDSC, acreage TaxlotAcre + MapAcres) — PLUS the
--   richest extra TERRA has seen on an ODF-family feed:
--     * VALUATION INLINE — ASSESSVAL (assessed total) + LANDVALUE + IMPVALUE.
--       This is LIVE, not IN FLIGHT (contrast Yamhill / Jefferson which ship NO
--       valuation, and Clatsop whose valuation is token-gated). Surface it.
--     * Account / tax status — ACCTSTATUS + TAXSTATUS.
--     * Tax routing — MA (maintenance area) / SA (special assessment) / NH
--       (neighborhood).
--
--   So fetch_tillamook_assessment + fetch_tillamook_records are BOTH SQL
--   pass-throughs over L1 inline — no scraper, no viewstate gating, no per-call
--   HTTP. The assessment surface ships valuation LIVE.
--
--   Per the empty-but-present-column lesson (KB_87): keep every source column even
--   if sparsely populated so upstream backfills surface transparently.
--
-- IN FLIGHT vs siblings (flag honestly per DOCTRINE-HONEST-IN-FLIGHT-01):
--   - Recorded sale PRICE: not exposed (INSTYEAR/INSTMONTH/INSTID/INSTTYPE inline,
--     price IN FLIGHT — same as Yamhill).
--   - Multi-instrument deed chain: only the most-recent instrument is inline.
--   - Zoning: no Tillamook zoning service located/ingested yet (Jackson-pattern
--     follow-up). PRPCLSDSC carries the use class (e.g. forest / EFU hints).
--   - RMV real-market value: source ships assessed (ASSESSVAL) + land + improvement;
--     a separate RMV figure is not in the feed (assessed is the surfaced number).
--
-- county_fips column seeds the eventual unified `parcels` table migration.

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_tillamook (
    id BIGSERIAL PRIMARY KEY,
    fid INTEGER UNIQUE,                          -- FID (source PK)
    county_fips TEXT NOT NULL DEFAULT '057',

    -- Taxlot identifiers
    taxlot TEXT NOT NULL,                        -- ORTaxlot (primary natural key, 29-char)
    maptaxlot TEXT,                              -- MapTaxlot (human-friendly variant)
    ortaxlot TEXT,                               -- ORTaxlot duplicate (explicit join clarity)
    map_taxlot_short TEXT,                       -- Taxlot (5-char lot suffix within map)
    map_number TEXT,                             -- MapNumber
    ormapnum TEXT,                               -- ORMapNum (full 24-char ORMap)
    special_int TEXT,                            -- SpecialInt

    -- PLSS
    plss_county SMALLINT,                        -- County
    plss_town SMALLINT,                          -- Town
    plss_town_part DOUBLE PRECISION,             -- TownPart
    plss_town_dir TEXT,                          -- TownDir
    plss_range SMALLINT,                         -- Range
    plss_range_part DOUBLE PRECISION,            -- RangePart
    plss_range_dir TEXT,                         -- RangeDir
    plss_section SMALLINT,                        -- SecNumber
    plss_qtr TEXT,                               -- Qtr
    plss_qtr_qtr TEXT,                           -- QtrQtr
    plss_anomaly TEXT,                           -- Anomaly

    -- Map suffix / class metadata
    map_suf_type TEXT,                           -- MapSufType
    map_suf_num INTEGER,                         -- MapSufNum
    map_class TEXT,                              -- MapClass
    map_rel_code TEXT,                           -- MapRelCode
    relia_code INTEGER,                          -- ReliaCode

    -- Account / per-parcel deep link
    prim_acc_num TEXT,                           -- PRIMACCNUM
    si_map_tax TEXT,                             -- SIMAPTAX
    ref_link TEXT,                               -- REFLink

    -- Ownership / mailing
    owner_line1 TEXT,                            -- OWNERLINE1 (primary fee owner)
    owner_line2 TEXT,                            -- OWNERLINE2 (co-owner / trustee 2)
    owner_line3 TEXT,                            -- OWNERLINE3 (co-owner / trustee 3)
    agent_name TEXT,                             -- AGENTNAME
    mail_add1 TEXT,                              -- MAILADD1
    mail_add2 TEXT,                              -- MAILADD2
    mail_city TEXT,                              -- MAILCITY
    mail_state TEXT,                             -- MAILSTATE
    mail_zip TEXT,                               -- MAILZIP
    mail_country TEXT,                           -- MAILCNTRY

    -- Situs
    site_add_nam TEXT,                           -- SITEADDNAM
    site_add_cty TEXT,                           -- SITEADDCTY
    site_zip TEXT,                               -- SITEZIP

    -- Last instrument (deed/transfer snapshot — inline in source)
    inst_year SMALLINT,                          -- INSTYEAR
    inst_month SMALLINT,                         -- INSTMONTH
    inst_id TEXT,                                -- INSTID
    inst_type TEXT,                              -- INSTTYPE

    -- Property classification
    dwelling TEXT,                               -- DWELLING (Y/N)
    prp_class TEXT,                              -- PRPCLASS
    prp_cls_desc TEXT,                           -- PRPCLSDSC

    -- Tax routing
    ma TEXT,                                     -- MA (maintenance area)
    sa TEXT,                                     -- SA (special assessment)
    nh TEXT,                                     -- NH (neighborhood)

    -- Valuation (LIVE — inline in source, unlike Yamhill/Jefferson/Clatsop)
    assessed_value DOUBLE PRECISION,             -- ASSESSVAL (assessed total)
    land_value DOUBLE PRECISION,                 -- LANDVALUE
    improvement_value DOUBLE PRECISION,          -- IMPVALUE

    -- Status
    acct_status TEXT,                            -- ACCTSTATUS
    tax_status TEXT,                             -- TAXSTATUS

    -- Acreage / area
    taxlot_acres DOUBLE PRECISION,               -- TaxlotAcre (polygon native)
    map_acres DOUBLE PRECISION,                  -- MapAcres
    taxlot_feet INTEGER,                         -- TaxlotFeet
    shape_area DOUBLE PRECISION,                 -- Shape__Area (source units)
    shape_length DOUBLE PRECISION,               -- Shape__Length (source units)

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_parcels_tillamook_geom ON parcels_tillamook USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_tillamook_taxlot ON parcels_tillamook(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_tillamook_maptaxlot ON parcels_tillamook(maptaxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_tillamook_map_number ON parcels_tillamook(map_number);
CREATE INDEX IF NOT EXISTS idx_parcels_tillamook_prim_acc_num ON parcels_tillamook(prim_acc_num);
CREATE INDEX IF NOT EXISTS idx_parcels_tillamook_owner_line1 ON parcels_tillamook(owner_line1);
CREATE INDEX IF NOT EXISTS idx_parcels_tillamook_site_add_cty ON parcels_tillamook(site_add_cty);
CREATE INDEX IF NOT EXISTS idx_parcels_tillamook_prp_class ON parcels_tillamook(prp_class);

COMMIT;
