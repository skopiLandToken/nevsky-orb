"""
Ingest Jackson County taxlot parcels into parcels_jackson, and zoning polygons
into jackson_zoning, in a single run.

Source:
  Taxlots — spatial.jacksoncountyor.gov OpenData/ReferenceData MapServer layer 3
            (on-prem ArcGIS Server). 104,751 polygons as of 2026-05-27.
  Zoning  — spatial.jacksoncountyor.gov DevServ/ZoningDistricts FeatureServer
            layer 0.

Strategy:
  Paginated REST query with outSR=4326 so the server reprojects from EPSG:2270
  (Oregon SP South NAD83 HARN ftUS) to WGS84 for us. Idempotent: ON CONFLICT
  (objectid) DO UPDATE refreshes existing rows.

Memory: holds at most BATCH_SIZE features in RAM at any moment.

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_jackson_parcels
"""
import os
import json
import time
import sys
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

TAXLOT_URL = (
    "https://spatial.jacksoncountyor.gov/arcgis/rest/services/"
    "OpenData/ReferenceData/MapServer/3/query"
)
ZONING_URL = (
    "https://spatial.jacksoncountyor.gov/arcgis/rest/services/"
    "DevServ/ZoningDistricts/FeatureServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 1000   # server max for both services
BATCH_SIZE = 500   # DB insert batch
HTTP_TIMEOUT = 90
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


PARCEL_INSERT_SQL = """
INSERT INTO parcels_jackson (
    objectid, taxlot, county_fips,
    map_number, map_num, tm_maplot,
    account, lottype, prop_class, tax_code,
    owner_name, contract_buyer, in_care_of,
    mailing_address_1, mailing_address_2, mailing_city, mailing_state, mailing_zip,
    situs_address, address_num, street_name,
    build_code, year_built, comm_sqft, lot_depth, lot_width,
    acreage, gis_area,
    land_value, imp_value, assess_land, assess_imp,
    maintenance, schedule_co, neighborhood,
    shape_length, shape_area,
    geom, raw_properties
) VALUES (
    %s, %s, '029',
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    map_number = EXCLUDED.map_number,
    map_num = EXCLUDED.map_num,
    tm_maplot = EXCLUDED.tm_maplot,
    account = EXCLUDED.account,
    lottype = EXCLUDED.lottype,
    prop_class = EXCLUDED.prop_class,
    tax_code = EXCLUDED.tax_code,
    owner_name = EXCLUDED.owner_name,
    contract_buyer = EXCLUDED.contract_buyer,
    in_care_of = EXCLUDED.in_care_of,
    mailing_address_1 = EXCLUDED.mailing_address_1,
    mailing_address_2 = EXCLUDED.mailing_address_2,
    mailing_city = EXCLUDED.mailing_city,
    mailing_state = EXCLUDED.mailing_state,
    mailing_zip = EXCLUDED.mailing_zip,
    situs_address = EXCLUDED.situs_address,
    address_num = EXCLUDED.address_num,
    street_name = EXCLUDED.street_name,
    build_code = EXCLUDED.build_code,
    year_built = EXCLUDED.year_built,
    comm_sqft = EXCLUDED.comm_sqft,
    lot_depth = EXCLUDED.lot_depth,
    lot_width = EXCLUDED.lot_width,
    acreage = EXCLUDED.acreage,
    gis_area = EXCLUDED.gis_area,
    land_value = EXCLUDED.land_value,
    imp_value = EXCLUDED.imp_value,
    assess_land = EXCLUDED.assess_land,
    assess_imp = EXCLUDED.assess_imp,
    maintenance = EXCLUDED.maintenance,
    schedule_co = EXCLUDED.schedule_co,
    neighborhood = EXCLUDED.neighborhood,
    shape_length = EXCLUDED.shape_length,
    shape_area = EXCLUDED.shape_area,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    ingested_at = NOW();
"""

ZONING_INSERT_SQL = """
INSERT INTO jackson_zoning (
    objectid, zone_code, zone_desc, comp_plan_des,
    base_elev, height_max, last_update, last_editor,
    shape_area, shape_length, geom, raw_properties
) VALUES (
    %s, %s, %s, %s,
    %s, %s, to_timestamp(%s/1000), %s,
    %s, %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    zone_code = EXCLUDED.zone_code,
    zone_desc = EXCLUDED.zone_desc,
    comp_plan_des = EXCLUDED.comp_plan_des,
    base_elev = EXCLUDED.base_elev,
    height_max = EXCLUDED.height_max,
    last_update = EXCLUDED.last_update,
    last_editor = EXCLUDED.last_editor,
    shape_area = EXCLUDED.shape_area,
    shape_length = EXCLUDED.shape_length,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    ingested_at = NOW();
"""


def fetch_page(base_url: str, offset: int, page_size: int) -> dict:
    """Pull one page of features with retries on transient HTTP errors."""
    url = (
        f"{base_url}?where=1%3D1"
        f"&outFields=*&outSR=4326&f=geojson"
        f"&resultOffset={offset}&resultRecordCount={page_size}"
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


def parcel_row(feature):
    props = feature.get("properties", {}) or {}
    geom = feature.get("geometry")
    if geom is None:
        return None
    objectid = to_int(props.get("OBJECTID"))
    taxlot = to_str(props.get("MAPLOT"))
    if objectid is None or taxlot is None:
        return None
    return (
        objectid,
        taxlot,
        to_str(props.get("MAPNUMBER")),
        to_str(props.get("MAPNUM")),
        to_str(props.get("TM_MAPLOT")),
        to_int(props.get("ACCOUNT")),
        to_str(props.get("LOTTYPE")),
        to_int(props.get("PROPCLASS")),
        to_int(props.get("TAXCODE")),
        to_str(props.get("FEEOWNER")),
        to_str(props.get("CONTRACT")),
        to_str(props.get("INCAREOF")),
        to_str(props.get("ADDRESS1")),
        to_str(props.get("ADDRESS2")),
        to_str(props.get("CITY")),
        to_str(props.get("STATE")),
        to_str(props.get("ZIPCODE")),
        to_str(props.get("SITEADD")),
        to_str(props.get("ADDRESSNUM")),
        to_str(props.get("STREETNAME")),
        to_int(props.get("BUILDCODE")),
        to_int(props.get("YEARBLT")),
        to_int(props.get("COMMSQFT")),
        to_int(props.get("LOTDEPTH")),
        to_int(props.get("LOTWIDTH")),
        to_float(props.get("ACREAGE")),
        to_float(props.get("GIS_AREA")),
        to_int(props.get("LANDVALUE")),
        to_int(props.get("IMPVALUE")),
        to_int(props.get("ASSESSLAND")),
        to_int(props.get("ASSESSIMP")),
        to_int(props.get("MAINTENANC")),
        to_int(props.get("SCHEDULECO")),
        to_int(props.get("NEIGHBORHO")),
        to_float(props.get("Shape__Length")),
        to_float(props.get("Shape__Area")),
        json.dumps(geom),
        json.dumps(props),
    )


def zoning_row(feature):
    props = feature.get("properties", {}) or {}
    geom = feature.get("geometry")
    if geom is None:
        return None
    objectid = to_int(props.get("OBJECTID"))
    if objectid is None:
        return None
    last_update = props.get("LASTUPDATE")
    try:
        last_update = int(last_update) if last_update is not None else 0
    except (TypeError, ValueError):
        last_update = 0
    return (
        objectid,
        to_str(props.get("ZONECLASS")),
        to_str(props.get("ZONEDESC")),
        to_str(props.get("COMPPLANDES")),
        to_float(props.get("BASEELEV")),
        to_float(props.get("HEIGHT")),
        last_update,
        to_str(props.get("LASTEDITOR")),
        to_float(props.get("Shape__Area")),
        to_float(props.get("Shape__Length")),
        json.dumps(geom),
        json.dumps(props),
    )


def ingest(conn, base_url: str, insert_sql: str, row_fn, label: str) -> int:
    t0 = time.time()
    total = 0
    skipped = 0
    offset = 0
    page_num = 0
    while True:
        page_num += 1
        page = fetch_page(base_url, offset, PAGE_SIZE)
        feats = page.get("features", []) or []
        if not feats:
            break

        batch = []
        for f in feats:
            row = row_fn(f)
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                with conn.cursor() as cur:
                    cur.executemany(insert_sql, batch)
                conn.commit()
                total += len(batch)
                batch = []

        if batch:
            with conn.cursor() as cur:
                cur.executemany(insert_sql, batch)
            conn.commit()
            total += len(batch)

        elapsed = time.time() - t0
        print(f"[{label}] page {page_num} offset={offset} got {len(feats)} | total: {total} | skipped: {skipped} | elapsed: {elapsed:.1f}s")

        # ArcGIS REST: fewer than PAGE_SIZE returned = last page (also true for FeatureServer
        # when exceededTransferLimit is absent)
        if len(feats) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    elapsed = time.time() - t0
    print(f"[{label}] DONE: {total} ingested, {skipped} skipped, {elapsed:.1f}s")
    return total


def main():
    conn = psycopg.connect(DB_DSN)
    conn.autocommit = False
    try:
        parcels = ingest(conn, TAXLOT_URL, PARCEL_INSERT_SQL, parcel_row, "parcels_jackson")
        zoning = ingest(conn, ZONING_URL, ZONING_INSERT_SQL, zoning_row, "jackson_zoning")
        print(f"\nALL DONE: parcels={parcels}, zoning_polys={zoning}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
