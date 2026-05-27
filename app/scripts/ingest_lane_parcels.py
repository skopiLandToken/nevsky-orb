"""
Ingest Lane County taxlot parcels into parcels_lane.

Source: services3.arcgis.com/NbWCmkRTtvyr63CT/.../Taxlot__Public/FeatureServer/0
        (Lane Geographic Data Consortium / LCOG-hosted ArcGIS Online feature service.)

Strategy: paginated REST query with outSR=4326 + f=geojson so the server reprojects
          from EPSG:2914 (Oregon North NAD83 HARN ftUS) to WGS84 for us. Single
          Polygon geometry from source → cast ST_Multi() on write to match
          parcels_deschutes/crook/multnomah/washington.

Idempotent: ON CONFLICT (objectid) DO UPDATE refreshes existing rows. Pagination
          uses orderByFields=OBJECTID + resultOffset which is stable across runs.

Memory: at most BATCH_SIZE features in RAM at any moment.

recordCount at authoring time (2026-05-27): 158,619 parcels → ~80 pages at 2000/page.

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_lane_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

SERVICE_URL = (
    "https://services3.arcgis.com/NbWCmkRTtvyr63CT/arcgis/rest/services/"
    "Taxlot__Public/FeatureServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 2000
BATCH_SIZE = 500
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


INSERT_SQL = """
INSERT INTO parcels_lane (
    objectid, taxlot, county_fips,
    maptaxlot_display, taxmap_number, supplemental_taxmap_number, taxlot_within_map,
    property_alt_key, taxlot_alt_key, reference_account_number,
    detailed_land_use_code, detailed_land_use_name,
    general_land_use_code, general_land_use_name,
    land_use_class, statistical_class_code, statistical_class_name,
    property_class_code, property_class_name,
    owner_count, reference_owner_name,
    address_count, reference_full_address,
    account_count, real_property_account_count, personal_property_account_count,
    tax_code_area, has_tax_code_split, exists_in_account, exists_in_taxmap,
    total_real_market_value, total_real_market_value_land,
    total_real_market_value_impr, total_real_market_value_pers,
    total_assessed_value, total_amount_exempted, total_taxable_value,
    total_tax_amount_certified, exemption_count, reference_exemption_type,
    improvement_count, reference_imprvmnt_type, reference_imprvmnt_year_built,
    total_impr_base_area_sqft, total_impr_finished_area_sqft, total_impr_unfinished_area_sqft,
    structure_count,
    school_district_code, school_district_area_percent,
    elem_school_institution_id, middle_school_institution_id, high_school_institution_id,
    fire_protection_provider_code, fire_protection_area_percent,
    neighborhood_association_name,
    census_tract_fips_code, census_block_group_fips_code, census_block_fips_code,
    census_tract_number, census_block_group_number, census_block_number,
    base_zone_count, base_zone_code, base_zone_area_percent,
    plan_designation_code, plan_designation_area_percent,
    planning_jurisdiction_code,
    urban_growth_boundary_code, urban_growth_area_percent,
    urban_reserve_code, urban_reserve_area_percent,
    greenway_name, greenway_area_percent,
    market_area_name,
    incorporated_city_code, incorporated_area_percent, year_incorporated,
    plat_count, reference_plat_name,
    legal_area_acres, legal_area_sqft,
    estimated_area_acres, estimated_area_sqft,
    perimeter_ft, longitude_center, latitude_center,
    rlid_link, global_id,
    shape_length, shape_area,
    geom, raw_properties, last_modified
) VALUES (
    %s, %s, '039',
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s,
    %s, %s,
    %s, %s, %s,
    %s, %s,
    %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s,
    %s, %s,
    %s, %s,
    %s, %s,
    %s,
    %s, %s, %s,
    %s, %s,
    %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s,
    ST_Multi(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))),
    %s::jsonb, %s
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    maptaxlot_display = EXCLUDED.maptaxlot_display,
    taxmap_number = EXCLUDED.taxmap_number,
    supplemental_taxmap_number = EXCLUDED.supplemental_taxmap_number,
    taxlot_within_map = EXCLUDED.taxlot_within_map,
    property_alt_key = EXCLUDED.property_alt_key,
    taxlot_alt_key = EXCLUDED.taxlot_alt_key,
    reference_account_number = EXCLUDED.reference_account_number,
    detailed_land_use_code = EXCLUDED.detailed_land_use_code,
    detailed_land_use_name = EXCLUDED.detailed_land_use_name,
    general_land_use_code = EXCLUDED.general_land_use_code,
    general_land_use_name = EXCLUDED.general_land_use_name,
    land_use_class = EXCLUDED.land_use_class,
    statistical_class_code = EXCLUDED.statistical_class_code,
    statistical_class_name = EXCLUDED.statistical_class_name,
    property_class_code = EXCLUDED.property_class_code,
    property_class_name = EXCLUDED.property_class_name,
    owner_count = EXCLUDED.owner_count,
    reference_owner_name = EXCLUDED.reference_owner_name,
    address_count = EXCLUDED.address_count,
    reference_full_address = EXCLUDED.reference_full_address,
    account_count = EXCLUDED.account_count,
    real_property_account_count = EXCLUDED.real_property_account_count,
    personal_property_account_count = EXCLUDED.personal_property_account_count,
    tax_code_area = EXCLUDED.tax_code_area,
    has_tax_code_split = EXCLUDED.has_tax_code_split,
    exists_in_account = EXCLUDED.exists_in_account,
    exists_in_taxmap = EXCLUDED.exists_in_taxmap,
    total_real_market_value = EXCLUDED.total_real_market_value,
    total_real_market_value_land = EXCLUDED.total_real_market_value_land,
    total_real_market_value_impr = EXCLUDED.total_real_market_value_impr,
    total_real_market_value_pers = EXCLUDED.total_real_market_value_pers,
    total_assessed_value = EXCLUDED.total_assessed_value,
    total_amount_exempted = EXCLUDED.total_amount_exempted,
    total_taxable_value = EXCLUDED.total_taxable_value,
    total_tax_amount_certified = EXCLUDED.total_tax_amount_certified,
    exemption_count = EXCLUDED.exemption_count,
    reference_exemption_type = EXCLUDED.reference_exemption_type,
    improvement_count = EXCLUDED.improvement_count,
    reference_imprvmnt_type = EXCLUDED.reference_imprvmnt_type,
    reference_imprvmnt_year_built = EXCLUDED.reference_imprvmnt_year_built,
    total_impr_base_area_sqft = EXCLUDED.total_impr_base_area_sqft,
    total_impr_finished_area_sqft = EXCLUDED.total_impr_finished_area_sqft,
    total_impr_unfinished_area_sqft = EXCLUDED.total_impr_unfinished_area_sqft,
    structure_count = EXCLUDED.structure_count,
    school_district_code = EXCLUDED.school_district_code,
    school_district_area_percent = EXCLUDED.school_district_area_percent,
    elem_school_institution_id = EXCLUDED.elem_school_institution_id,
    middle_school_institution_id = EXCLUDED.middle_school_institution_id,
    high_school_institution_id = EXCLUDED.high_school_institution_id,
    fire_protection_provider_code = EXCLUDED.fire_protection_provider_code,
    fire_protection_area_percent = EXCLUDED.fire_protection_area_percent,
    neighborhood_association_name = EXCLUDED.neighborhood_association_name,
    census_tract_fips_code = EXCLUDED.census_tract_fips_code,
    census_block_group_fips_code = EXCLUDED.census_block_group_fips_code,
    census_block_fips_code = EXCLUDED.census_block_fips_code,
    census_tract_number = EXCLUDED.census_tract_number,
    census_block_group_number = EXCLUDED.census_block_group_number,
    census_block_number = EXCLUDED.census_block_number,
    base_zone_count = EXCLUDED.base_zone_count,
    base_zone_code = EXCLUDED.base_zone_code,
    base_zone_area_percent = EXCLUDED.base_zone_area_percent,
    plan_designation_code = EXCLUDED.plan_designation_code,
    plan_designation_area_percent = EXCLUDED.plan_designation_area_percent,
    planning_jurisdiction_code = EXCLUDED.planning_jurisdiction_code,
    urban_growth_boundary_code = EXCLUDED.urban_growth_boundary_code,
    urban_growth_area_percent = EXCLUDED.urban_growth_area_percent,
    urban_reserve_code = EXCLUDED.urban_reserve_code,
    urban_reserve_area_percent = EXCLUDED.urban_reserve_area_percent,
    greenway_name = EXCLUDED.greenway_name,
    greenway_area_percent = EXCLUDED.greenway_area_percent,
    market_area_name = EXCLUDED.market_area_name,
    incorporated_city_code = EXCLUDED.incorporated_city_code,
    incorporated_area_percent = EXCLUDED.incorporated_area_percent,
    year_incorporated = EXCLUDED.year_incorporated,
    plat_count = EXCLUDED.plat_count,
    reference_plat_name = EXCLUDED.reference_plat_name,
    legal_area_acres = EXCLUDED.legal_area_acres,
    legal_area_sqft = EXCLUDED.legal_area_sqft,
    estimated_area_acres = EXCLUDED.estimated_area_acres,
    estimated_area_sqft = EXCLUDED.estimated_area_sqft,
    perimeter_ft = EXCLUDED.perimeter_ft,
    longitude_center = EXCLUDED.longitude_center,
    latitude_center = EXCLUDED.latitude_center,
    rlid_link = EXCLUDED.rlid_link,
    global_id = EXCLUDED.global_id,
    shape_length = EXCLUDED.shape_length,
    shape_area = EXCLUDED.shape_area,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    last_modified = EXCLUDED.last_modified,
    ingested_at = NOW();
"""


def fetch_page(offset: int) -> dict:
    url = (
        f"{SERVICE_URL}?where=1%3D1"
        f"&outFields=*&outSR=4326&f=geojson"
        f"&resultOffset={offset}&resultRecordCount={PAGE_SIZE}"
        f"&orderByFields=OBJECTID"
    )
    req = Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            if attempt < HTTP_RETRIES:
                print(f"  HTTP attempt {attempt} failed: {e}; retrying in {HTTP_RETRY_DELAY}s")
                time.sleep(HTTP_RETRY_DELAY)
    raise RuntimeError(f"fetch_page offset={offset} failed after {HTTP_RETRIES} attempts: {last_err}")


def to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def to_ts_ms(v):
    """ArcGIS dates come as epoch milliseconds. Convert to ISO 8601 UTC for Postgres."""
    if v is None or v == "":
        return None
    try:
        ms = int(v)
        return time.strftime("%Y-%m-%d %H:%M:%S+00", time.gmtime(ms / 1000))
    except (TypeError, ValueError):
        return None


def feature_to_row(feature):
    props = feature.get("properties", {}) or {}
    geom = feature.get("geometry")
    if geom is None:
        return None
    objectid = to_int(props.get("OBJECTID"))
    taxlot = to_str(props.get("maptaxlot"))
    if objectid is None or taxlot is None:
        return None
    return (
        objectid,
        taxlot,
        to_str(props.get("maptaxlot_display")),
        to_str(props.get("taxmap_number")),
        to_str(props.get("supplemental_taxmap_number")),
        to_str(props.get("taxlot")),
        to_str(props.get("property_alt_key")),
        to_str(props.get("taxlot_alt_key")),
        to_int(props.get("reference_account_number")),
        to_str(props.get("detailed_land_use_code")),
        to_str(props.get("detailed_land_use_name")),
        to_str(props.get("general_land_use_code")),
        to_str(props.get("general_land_use_name")),
        to_str(props.get("land_use_class")),
        to_str(props.get("statistical_class_code")),
        to_str(props.get("statistical_class_name")),
        to_str(props.get("property_class_code")),
        to_str(props.get("property_class_name")),
        to_int(props.get("owner_count")),
        to_str(props.get("reference_owner_name")),
        to_int(props.get("address_count")),
        to_str(props.get("reference_full_address")),
        to_int(props.get("account_count")),
        to_int(props.get("real_property_account_count")),
        to_int(props.get("personal_property_account_count")),
        to_str(props.get("tax_code_area")),
        to_str(props.get("has_tax_code_split")),
        to_str(props.get("exists_in_account")),
        to_str(props.get("exists_in_taxmap")),
        to_int(props.get("total_real_market_value")),
        to_int(props.get("total_real_market_value_land")),
        to_int(props.get("total_real_market_value_impr")),
        to_int(props.get("total_real_market_value_pers")),
        to_int(props.get("total_assessed_value")),
        to_int(props.get("total_amount_exempted")),
        to_int(props.get("total_taxable_value")),
        to_float(props.get("total_tax_amount_certified")),
        to_int(props.get("exemption_count")),
        to_str(props.get("reference_exemption_type")),
        to_int(props.get("improvement_count")),
        to_str(props.get("reference_imprvmnt_type")),
        to_int(props.get("reference_imprvmnt_year_built")),
        to_int(props.get("total_impr_base_area_sqft")),
        to_int(props.get("total_impr_finished_area_sqft")),
        to_int(props.get("total_impr_unfinished_area_sqft")),
        to_int(props.get("structure_count")),
        to_str(props.get("school_district_code")),
        to_float(props.get("school_district_area_percent")),
        to_int(props.get("elem_school_institution_id")),
        to_int(props.get("middle_school_institution_id")),
        to_int(props.get("high_school_institution_id")),
        to_str(props.get("fire_protection_provider_code")),
        to_float(props.get("fire_protection_area_percent")),
        to_str(props.get("neighborhood_association_name")),
        to_str(props.get("census_tract_fips_code")),
        to_str(props.get("census_block_group_fips_code")),
        to_str(props.get("census_block_fips_code")),
        to_str(props.get("census_tract_number")),
        to_str(props.get("census_block_group_number")),
        to_str(props.get("census_block_number")),
        to_int(props.get("base_zone_count")),
        to_str(props.get("base_zone_code")),
        to_float(props.get("base_zone_area_percent")),
        to_str(props.get("plan_designation_code")),
        to_float(props.get("plan_designation_area_percent")),
        to_str(props.get("planning_jurisdiction_code")),
        to_str(props.get("urban_growth_boundary_code")),
        to_float(props.get("urban_growth_area_percent")),
        to_str(props.get("urban_reserve_code")),
        to_float(props.get("urban_reserve_area_percent")),
        to_str(props.get("greenway_name")),
        to_float(props.get("greenway_area_percent")),
        to_str(props.get("market_area_name")),
        to_str(props.get("incorporated_city_code")),
        to_float(props.get("incorporated_area_percent")),
        to_int(props.get("year_incorporated")),
        to_int(props.get("plat_count")),
        to_str(props.get("reference_plat_name")),
        to_float(props.get("legal_area_acres")),
        to_float(props.get("legal_area_sqft")),
        to_float(props.get("estimated_area_acres")),
        to_float(props.get("estimated_area_sqft")),
        to_float(props.get("perimeter_ft")),
        to_float(props.get("longitude_center")),
        to_float(props.get("latitude_center")),
        to_str(props.get("rlid_link")),
        to_str(props.get("GlobalID")),
        to_float(props.get("Shape__Length")),
        to_float(props.get("Shape__Area")),
        json.dumps(geom),
        json.dumps(props),
        to_ts_ms(props.get("feature_date_updated")),
    )


def main():
    t0 = time.time()
    conn = psycopg.connect(DB_DSN)
    conn.autocommit = False
    total = 0
    skipped = 0
    offset = 0
    page_num = 0

    try:
        while True:
            page_num += 1
            page = fetch_page(offset)
            feats = page.get("features", []) or []
            if not feats:
                break

            batch = []
            for f in feats:
                row = feature_to_row(f)
                if row is None:
                    skipped += 1
                    continue
                batch.append(row)
                if len(batch) >= BATCH_SIZE:
                    with conn.cursor() as cur:
                        cur.executemany(INSERT_SQL, batch)
                    conn.commit()
                    total += len(batch)
                    batch = []

            if batch:
                with conn.cursor() as cur:
                    cur.executemany(INSERT_SQL, batch)
                conn.commit()
                total += len(batch)

            elapsed = time.time() - t0
            print(
                f"page {page_num} offset={offset} got {len(feats)} | "
                f"total ingested: {total} | skipped: {skipped} | elapsed: {elapsed:.1f}s",
                flush=True,
            )

            if len(feats) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

    finally:
        conn.close()

    elapsed = time.time() - t0
    print(f"\nDONE: {total} parcels ingested, {skipped} skipped, {elapsed:.1f}s")


if __name__ == "__main__":
    main()
