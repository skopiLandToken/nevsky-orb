-- parcels_lane: Lane County (FIPS 41-039) taxlot polygons + native attribute fields.
--
-- Source: services3.arcgis.com/NbWCmkRTtvyr63CT/.../Taxlot__Public/FeatureServer/0
--         (Lane Geographic Data Consortium / LCOG-hosted ArcGIS Online feature service.)
-- Source SR: WKID 2914 (Oregon North NAD83 HARN ftUS); pulled with outSR=4326 so server reprojects.
-- Storage SR: 4326 (matches parcels_deschutes/crook/multnomah/washington for cross-county unions).
-- Source geometry: esriGeometryPolygon (single Polygon).
-- Cast: ST_Multi() during ingest so geom type is MultiPolygon(4326) uniformly across counties.
--
-- recordCount at ingest authoring time (2026-05-27): 158,619 parcels.
--
-- Schema reality vs Multnomah / Washington:
--   Lane ships the RICHEST inline attribute set of any Oregon county TERRA has integrated.
--   The LGDC service surfaces, per parcel:
--     - Ownership (single reference_owner_name field; full owner list via related table 100)
--     - Address (single reference_full_address)
--     - Full valuation: RMV land + impr + personal, Assessed Value, Taxable Value, Tax Certified
--     - Zoning (base_zone_code), plan designation, UGB code, urban-reserve code
--     - Census tract / block / block-group FIPS
--     - School + fire-district + neighborhood association
--     - Plat name, market area, planning jurisdiction, incorporated city
--     - rlid_link — direct per-parcel deep link to the LCOG RLID property report
--       (RLID itself is subscription-gated, but the URL is the canonical deeplink the
--        service expects callers to hand off to; honoring the field surfaces the right
--        outbound link from L2 without trying to scrape the paywalled report.)
--   That means Layer 2 PropTax + zoning + jurisdiction for Lane are answered DIRECTLY
--   from L1 inline data — no upstream lazy-fetch needed for the snapshot. Recorded
--   documents / deed chain / sales history live with the Lane County Clerk recordings
--   office, which does NOT publish a queryable public API (flag as IN FLIGHT in
--   fetch_lane_records — surface what we have and link out).
--
-- Note on integer widths:
--   Source schema declares RMV / AV / tax fields as esriFieldTypeInteger (32-bit).
--   The largest values observed in Lane (state-owned UO parcels at $1B+ RMV) stay
--   well under INT4 max, but aggregate downstream queries (county-wide RMV sums) will
--   blow INT4. Store everything as BIGINT so SUM() works without explicit casts.
--
-- county_fips column is the seed for the eventual unified `parcels` table migration
-- (queued post-3-county-expansion per parcels_crook migration note).

BEGIN;

CREATE TABLE IF NOT EXISTS parcels_lane (
    id BIGSERIAL PRIMARY KEY,
    objectid INTEGER UNIQUE,
    taxlot TEXT NOT NULL,                      -- maptaxlot (13-char concatenated)
    county_fips TEXT NOT NULL DEFAULT '039',

    -- Native identifiers
    maptaxlot_display TEXT,                    -- hyphenated form, e.g. '17-04-35-32-00500'
    taxmap_number TEXT,                        -- 8-char taxmap sheet ID, e.g. '17043532'
    supplemental_taxmap_number TEXT,
    taxlot_within_map TEXT,                    -- last 5 digits, e.g. '00500' (called `taxlot` in source)
    property_alt_key TEXT,                     -- hash key linking to other LGDC services
    taxlot_alt_key TEXT,                       -- same hash, different domain
    reference_account_number INTEGER,          -- Lane A&T account number

    -- Land use / class (Lane has 4 layers of land-use coding)
    detailed_land_use_code TEXT,               -- 'LU-2100'
    detailed_land_use_name TEXT,               -- 'Commercial Improved'
    general_land_use_code TEXT,                -- '21'
    general_land_use_name TEXT,
    land_use_class TEXT,                       -- 'Commercial' / 'Residential' / etc.
    statistical_class_code TEXT,               -- e.g. '441'
    statistical_class_name TEXT,               -- 'Retail, Multi Tenant'
    property_class_code TEXT,                  -- e.g. '201'
    property_class_name TEXT,                  -- 'Commercial, Improved'

    -- Native ownership + addressing (would be lazy-fetch on Deschutes/Washington)
    owner_count INTEGER,
    reference_owner_name TEXT,                 -- primary owner; full ownership list lives in related table 100
    address_count INTEGER,
    reference_full_address TEXT,               -- single canonical situs address string

    -- Account / tax routing
    account_count INTEGER,
    real_property_account_count INTEGER,
    personal_property_account_count INTEGER,
    tax_code_area TEXT,
    has_tax_code_split TEXT,
    exists_in_account TEXT,                    -- Y/N flags from LGDC referential integrity
    exists_in_taxmap TEXT,

    -- Full inline valuation (Lane's headline advantage)
    total_real_market_value BIGINT,            -- RMV total
    total_real_market_value_land BIGINT,
    total_real_market_value_impr BIGINT,
    total_real_market_value_pers BIGINT,
    total_assessed_value BIGINT,               -- AV (Measure 50 capped)
    total_amount_exempted BIGINT,
    total_taxable_value BIGINT,
    total_tax_amount_certified DOUBLE PRECISION,
    exemption_count INTEGER,
    reference_exemption_type TEXT,

    -- Improvements
    improvement_count INTEGER,
    reference_imprvmnt_type TEXT,              -- 'General Retail' / 'Single Family Residential' / etc.
    reference_imprvmnt_year_built SMALLINT,
    total_impr_base_area_sqft INTEGER,
    total_impr_finished_area_sqft INTEGER,
    total_impr_unfinished_area_sqft INTEGER,
    structure_count INTEGER,

    -- Districts
    school_district_code TEXT,                 -- '4J' / etc.
    school_district_area_percent DOUBLE PRECISION,
    elem_school_institution_id SMALLINT,
    middle_school_institution_id SMALLINT,
    high_school_institution_id SMALLINT,
    fire_protection_provider_code TEXT,        -- 'EGF' / etc.
    fire_protection_area_percent DOUBLE PRECISION,
    neighborhood_association_name TEXT,

    -- Census
    census_tract_fips_code TEXT,
    census_block_group_fips_code TEXT,
    census_block_fips_code TEXT,
    census_tract_number TEXT,
    census_block_group_number TEXT,
    census_block_number TEXT,

    -- Zoning + planning
    base_zone_count INTEGER,
    base_zone_code TEXT,                       -- 'C-2' / 'R-1' / 'PL' (Public Land) / etc.
    base_zone_area_percent DOUBLE PRECISION,
    plan_designation_code TEXT,                -- 'MTP' / 'LDR' / etc.
    plan_designation_area_percent DOUBLE PRECISION,
    planning_jurisdiction_code TEXT,           -- 'EUG' / 'SPR' / 'LAN' (unincorporated)
    urban_growth_boundary_code TEXT,
    urban_growth_area_percent DOUBLE PRECISION,
    urban_reserve_code TEXT,
    urban_reserve_area_percent DOUBLE PRECISION,
    greenway_name TEXT,
    greenway_area_percent DOUBLE PRECISION,
    market_area_name TEXT,
    incorporated_city_code TEXT,               -- 'EUG' / 'SPR' / etc.
    incorporated_area_percent DOUBLE PRECISION,
    year_incorporated SMALLINT,

    -- Plat
    plat_count INTEGER,
    reference_plat_name TEXT,

    -- Area (Lane ships THREE acreage figures — legal/estimated/shape — keep all three)
    legal_area_acres DOUBLE PRECISION,         -- assessed legal acreage
    legal_area_sqft DOUBLE PRECISION,
    estimated_area_acres DOUBLE PRECISION,     -- estimated from geometry
    estimated_area_sqft DOUBLE PRECISION,
    perimeter_ft DOUBLE PRECISION,
    longitude_center DOUBLE PRECISION,         -- pre-computed centroid
    latitude_center DOUBLE PRECISION,

    -- Native deep link to LCOG RLID (gated, but canonical)
    rlid_link TEXT,
    global_id TEXT,                            -- GlobalID for related-table joins

    -- Shape metrics + geometry
    shape_length DOUBLE PRECISION,             -- Shape__Length
    shape_area DOUBLE PRECISION,               -- Shape__Area
    geom GEOMETRY(MultiPolygon, 4326),

    raw_properties JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMPTZ                  -- feature_date_updated (epoch ms in source)
);

CREATE INDEX IF NOT EXISTS idx_parcels_lane_geom ON parcels_lane USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_parcels_lane_taxlot ON parcels_lane(taxlot);
CREATE INDEX IF NOT EXISTS idx_parcels_lane_taxmap_number ON parcels_lane(taxmap_number);
CREATE INDEX IF NOT EXISTS idx_parcels_lane_account ON parcels_lane(reference_account_number);
CREATE INDEX IF NOT EXISTS idx_parcels_lane_base_zone_code ON parcels_lane(base_zone_code);
CREATE INDEX IF NOT EXISTS idx_parcels_lane_owner_name ON parcels_lane(reference_owner_name);
CREATE INDEX IF NOT EXISTS idx_parcels_lane_address ON parcels_lane(reference_full_address);
CREATE INDEX IF NOT EXISTS idx_parcels_lane_jurisdiction ON parcels_lane(planning_jurisdiction_code);
CREATE INDEX IF NOT EXISTS idx_parcels_lane_incorporated_city ON parcels_lane(incorporated_city_code);

COMMIT;
