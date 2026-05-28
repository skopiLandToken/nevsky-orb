-- parcels_polk: Polk County (FIPS 053) taxlot polygons + native attribute fields.
--
-- Source: maps.co.polk.or.us/gis/rest/services/Assessor/Taxlots/MapServer/11
--         (Polk County ArcGIS Server 11.4 — NOTE: served at /gis/rest, not /arcgis/rest.)
-- Source SR: EPSG:2913 (Oregon Statewide Lambert (Intl Ft, NAD83/HARN))
--         — server reprojects to 4326 on outSR=4326 + f=geojson.
-- Storage SR: 4326 (matches all other county tables).
-- Geometry: esriGeometryPolygon (single) — cast ST_Multi() on write so geom type
-- is MultiPolygon(4326) uniformly across counties.
--
-- recordCount at ingest authoring time (2026-05-28): 35,274 parcels.
--
-- Schema note vs siblings:
--   Polk's Taxlots service ships a joined Marion-style attribute set — taxlot
--   polygon attrs + assessor account attrs already joined in one row. Owner
--   (OwnerLine1, AgentName), mailing block (MailAdd1/2/City/State/Zip/Cntry),
--   situs (SiteAddNam/SiteAddCty/SiteZip), last instrument (InstYear/Month/Id/
--   Type), property class (PrpClass/PrpClsDsc), valuation (AstImpVal/AstLndVal/
--   AstValue), acreage (TaxlotAcre + Acct_Acres), tax routing (TaxCodeArea/
--   TaxCode/SA/MA/NH/Unit_ID), and account_id all inline. This means Layer 2
--   assessment/records can be SQL pass-throughs over parcels_polk (Marion
--   pattern), no per-account scrape needed for the snapshot fields.
--
-- Note: source does NOT ship zoning, building details (year built / sqft), RMV
-- (real-market value) family, or sale price. Those are IN FLIGHT — sale price
-- in particular requires the linked PCMAPS.DBO.V_REAL_SALES table (relationship
-- on the MapServer, layer 12) which is queryable on demand from Layer 2.
--
-- county_fips column seeds the eventual unified `parcels` table migration.

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_polk (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    county_fips TEXT NOT NULL DEFAULT '053',

    -- Taxlot identifiers
    taxlot TEXT NOT NULL,                       -- ORTaxlot (primary natural key, 29-char)
    maptaxlot TEXT,                             -- MapTaxlot (human-friendly variant)
    ortaxlot TEXT,                              -- ORTaxlot duplicate (kept for explicit join clarity)
    map_taxlot_short TEXT,                      -- TAXLOT (5-char lot suffix within map)
    map_number TEXT,                            -- MapNumber (7.5.29 style)
    ormapnum TEXT,                              -- ORMapNum (full 24-char ORMap)
    special_int TEXT,                           -- SpecialInt

    -- Account
    account_id BIGINT,                          -- AcctTable_Account_ID
    prim_acc_num TEXT,                          -- AcctTable_PrimAccNum
    si_map_tax TEXT,                            -- AcctTable_SIMapTax

    -- Ownership / mailing
    owner_line1 TEXT,                           -- AcctTable_OwnerLine1
    agent_name TEXT,                            -- AcctTable_AgentName
    in_care_of TEXT,                            -- AcctTable_In_Care_Of
    mail_add1 TEXT,                             -- AcctTable_MailAdd1
    mail_add2 TEXT,                             -- AcctTable_MailAdd2
    mail_city TEXT,                             -- AcctTable_MailCity
    mail_state TEXT,                            -- AcctTable_MailState
    mail_zip TEXT,                              -- AcctTable_MailZip
    mail_country TEXT,                          -- AcctTable_MailCntry

    -- Situs
    site_add_nam TEXT,                          -- AcctTable_SiteAddNam (street/number)
    site_add_cty TEXT,                          -- AcctTable_SiteAddCty
    site_zip TEXT,                              -- AcctTable_SiteZip

    -- Last instrument (deed/transfer snapshot)
    inst_year SMALLINT,                         -- AcctTable_InstYear
    inst_month SMALLINT,                        -- AcctTable_InstMonth
    inst_id TEXT,                               -- AcctTable_InstId (e.g. "2020-16131")
    inst_type TEXT,                             -- AcctTable_InstType (WARRANTY DEED, etc)

    -- Property classification
    dwelling TEXT,                              -- AcctTable_Dwelling (Y/N)
    prp_class TEXT,                             -- AcctTable_PrpClass
    prp_cls_desc TEXT,                          -- AcctTable_PrpClsDsc

    -- Valuation (assessed; RMV family not present in source — see IN FLIGHT)
    ast_imp_val BIGINT,                         -- AcctTable_AstImpVal
    ast_lnd_val BIGINT,                         -- AcctTable_AstLndVal
    ast_value BIGINT,                           -- AcctTable_AstValue

    -- Tax routing
    sa TEXT,                                    -- AcctTable_SA
    ma TEXT,                                    -- AcctTable_MA
    nh TEXT,                                    -- AcctTable_NH
    tax_code TEXT,                              -- AcctTable_TaxCode
    tax_code_area TEXT,                         -- AcctTable_TaxCodeArea
    unit_id BIGINT,                             -- AcctTable_Unit_ID

    -- Acreage / area
    taxlot_acres DOUBLE PRECISION,              -- TaxlotTemp_TaxlotAcre (polygon native)
    taxlot_feet INTEGER,                        -- TaxlotTemp_TaxlotFeet
    acct_acres DOUBLE PRECISION,                -- AcctTable_Acct_Acres (assessor of record)
    acct_sqft TEXT,                             -- AcctTable_Acct_SqFt
    shape_area DOUBLE PRECISION,                -- Shape.STArea() in source units (sq ft)
    shape_length DOUBLE PRECISION,              -- Shape.STLength() in source units (ft)

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ                   -- no native LASTUPDATE in source; set on delta refresh
);

CREATE INDEX IF NOT EXISTS idx_parcels_polk_geom ON parcels_polk USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_polk_taxlot ON parcels_polk(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_polk_maptaxlot ON parcels_polk(maptaxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_polk_map_number ON parcels_polk(map_number);
CREATE INDEX IF NOT EXISTS idx_parcels_polk_account_id ON parcels_polk(account_id);
CREATE INDEX IF NOT EXISTS idx_parcels_polk_owner_line1 ON parcels_polk(owner_line1);
CREATE INDEX IF NOT EXISTS idx_parcels_polk_site_add_cty ON parcels_polk(site_add_cty);
CREATE INDEX IF NOT EXISTS idx_parcels_polk_prp_class ON parcels_polk(prp_class);

COMMIT;
