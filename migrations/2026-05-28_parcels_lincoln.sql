-- parcels_lincoln: Lincoln County (FIPS 41-041) taxlot polygons + native attribute fields.
--
-- Source (L1, full county): https://services3.arcgis.com/MiIvpOH5WqZfaAvr/arcgis/
--         rest/services/taxlot21/FeatureServer/0
--         (Hosted ArcGIS Online ORMAP cadastre layer — "taxlot21", county code 21.
--         Lincoln County does NOT run an ArcGIS Server: its public map is GeoMOOSE
--         backed by UMN MapServer/WFS (a_assessment.map), which is NOT ArcGIS-
--         queryable. The ODF statewide MapServer has query disabled. So this hosted
--         AGO cadastre layer is the canonical reachable L1 source.)
-- Source SR: 3857; server reprojects to 4326 on outSR=4326 + f=geojson.
-- Storage SR: 4326. Geometry: esriGeometryPolygon → ST_Multi(ST_CollectionExtract(
--             ST_MakeValid(...),3)) on write.
--
-- Parcel count at discovery: 46,852 (returnCountOnly). maxRecordCount = 2000.
-- Source PK is FID.
--
-- THIN-L1 / WASHINGTON-PATTERN ship (honest per DOCTRINE-HONEST-IN-FLIGHT-01):
--   The taxlot21 cadastre layer ships ONLY ORMAP polygon attributes — taxlot
--   identifiers (ORTaxlot / MapTaxlot / Taxlot / MapNumber / ORMapNum / SpecialInt),
--   full PLSS (County / Town / Range / SecNumber / Qtr / QtrQtr), map suffix/class,
--   acreage (TaxlotAcre + TaxLotFeet), and per-parcel REFLink. There is **NO owner /
--   situs / mailing / account / valuation / instrument** on this layer (mirrors the
--   Washington L1 ship: geometry + taxlot + acreage; everything else resolved later).
--
--   So this migration models the FULL Yamhill/Jefferson family schema (owner /
--   mailing / situs / instrument / class / valuation columns all present) even though
--   taxlot21 leaves them NULL on first ingest. Per the empty-but-present-column
--   lesson (KB_87) this lets the queued enrichment backfill transparently with no
--   schema migration.
--
-- ENRICHMENT PATH (queued follow-up, documented for the next session):
--   The Newport UGB rich layer
--     services.arcgis.com/Nse9mKSgB2fD47Hg/.../Taxlot_2023_Newport_UGB/FeatureServer/0
--   carries the FULL attribute set (OwnerLine1/2/3, MailAdd, situsall, PrimAccNum,
--   PrpClass/PrpClsDsc, InstYear/InstMonth/InstID/InstType, TaxlotAcre) for the
--   ~8,373 Newport-UGB parcels (18% of county — the flagship coastal market). An
--   ORTaxlot-keyed UPDATE backfills owner/situs/instrument for Newport without a
--   schema change. County-wide owner/situs lives only behind the GeoMOOSE/WFS
--   a_assessment.map (OGC WFS GetFeature, GML parse) — a larger follow-up.
--
-- county_fips column seeds the eventual unified `parcels` table migration.

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_lincoln (
    id BIGSERIAL PRIMARY KEY,
    fid INTEGER UNIQUE,                          -- FID (source PK)
    county_fips TEXT NOT NULL DEFAULT '041',

    -- Taxlot identifiers (populated by taxlot21 L1)
    taxlot TEXT NOT NULL,                        -- ORTaxlot (primary natural key, 29-char)
    maptaxlot TEXT,                              -- MapTaxlot (human-friendly variant)
    ortaxlot TEXT,                               -- ORTaxlot duplicate (explicit join clarity)
    map_taxlot_short TEXT,                       -- Taxlot (5-char lot suffix within map)
    map_number TEXT,                             -- MapNumber
    ormapnum TEXT,                               -- ORMapNum (full 24-char ORMap)
    special_int TEXT,                            -- SpecialInt

    -- PLSS (populated by taxlot21 L1)
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

    -- Map suffix / class metadata (populated by taxlot21 L1)
    map_suf_type TEXT,                           -- MapSufType
    map_suf_num INTEGER,                         -- MapSufNum
    map_class TEXT,                              -- MapClass
    map_rel_code TEXT,                           -- MapRelCode
    relia_code INTEGER,                          -- ReliaCode
    ref_link TEXT,                               -- REFLink

    -- Ownership / mailing (NULL on first ingest — Newport-UGB enrichment backfills)
    prim_acc_num TEXT,                           -- (Newport UGB: PrimAccNum)
    owner_line1 TEXT,                            -- (Newport UGB: OwnerLine1)
    owner_line2 TEXT,                            -- (Newport UGB: OwnerLine2)
    owner_line3 TEXT,                            -- (Newport UGB: OwnerLine3)
    mail_add1 TEXT,
    mail_add2 TEXT,
    mail_city TEXT,
    mail_state TEXT,
    mail_zip TEXT,

    -- Situs (NULL on first ingest — enrichment backfills)
    site_add_nam TEXT,                           -- (Newport UGB: situsall)
    site_add_cty TEXT,
    site_zip TEXT,

    -- Instrument (NULL on first ingest — enrichment backfills)
    inst_year SMALLINT,
    inst_month SMALLINT,
    inst_id TEXT,
    inst_type TEXT,

    -- Classification / valuation (NULL on first ingest — enrichment backfills)
    dwelling TEXT,
    prp_class TEXT,                              -- (Newport UGB: PrpClass)
    prp_cls_desc TEXT,                           -- (Newport UGB: PrpClsDsc)
    assessed_value DOUBLE PRECISION,
    land_value DOUBLE PRECISION,
    improvement_value DOUBLE PRECISION,

    -- Acreage / area (populated by taxlot21 L1)
    taxlot_acres DOUBLE PRECISION,               -- TaxlotAcre
    taxlot_feet INTEGER,                         -- TaxLotFeet
    shape_area DOUBLE PRECISION,                 -- Shape__Area
    shape_length DOUBLE PRECISION,               -- Shape__Length

    geom GEOMETRY(MultiPolygon, 4326),
    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_parcels_lincoln_geom ON parcels_lincoln USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_lincoln_taxlot ON parcels_lincoln(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_lincoln_maptaxlot ON parcels_lincoln(maptaxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_lincoln_map_number ON parcels_lincoln(map_number);
CREATE INDEX IF NOT EXISTS idx_parcels_lincoln_prim_acc_num ON parcels_lincoln(prim_acc_num);
CREATE INDEX IF NOT EXISTS idx_parcels_lincoln_owner_line1 ON parcels_lincoln(owner_line1);

COMMIT;
