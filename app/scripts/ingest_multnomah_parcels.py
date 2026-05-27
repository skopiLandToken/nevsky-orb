"""
Ingest Multnomah County taxlot parcels into parcels_multnomah.

Source: services5.arcgis.com hosted feature service
        Multnomah_County_Taxlot_Parcels/FeatureServer/0
        (surfaced via gis-multco.opendata.arcgis.com Hub).

Strategy: paginated REST query with outSR=4326 + f=geojson so the server
          reprojects to WGS84 for us. Single Polygon geometry from source
          → cast ST_Multi() on write to match parcels_deschutes/parcels_crook.

Idempotent: ON CONFLICT (objectid) DO UPDATE refreshes existing rows.

Memory: at most BATCH_SIZE features in RAM at any moment.

recordCount at authoring time: 284,293 parcels → 143 pages at 2000/page.

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_multnomah_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

SERVICE_URL = (
    "https://services5.arcgis.com/x7DNZL1YqNQVNykA/arcgis/rest/services/"
    "Multnomah_County_Taxlot_Parcels/FeatureServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 2000
BATCH_SIZE = 500
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


INSERT_SQL = """
INSERT INTO parcels_multnomah (
    objectid, taxlot, county_fips,
    propid, alt_account_num,
    owner_name, owner_name_2,
    mailing_address, mailing_address_2, mailing_city, mailing_state, mailing_zip,
    situs_address, situs_city, situs_state, situs_zip,
    map_id, legal_desc, tract_lot, block, add_legal, township_range, assessor_map,
    loc_code, account_status, levy_code, nbo_code, imp_count, prop_class, prop_code,
    zone_code,
    deed_type, deed_date, inst_num, sale_price, sale_date, exemption,
    size_acres, size_sqft, imp_type, act_year_built, main_area, units, main_sqft,
    roll_year, roll_land, roll_imp, roll_m50,
    shape_length, shape_area,
    geom, raw_properties
) VALUES (
    %s, %s, '051',
    %s, %s,
    %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s,
    %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    propid = EXCLUDED.propid,
    alt_account_num = EXCLUDED.alt_account_num,
    owner_name = EXCLUDED.owner_name,
    owner_name_2 = EXCLUDED.owner_name_2,
    mailing_address = EXCLUDED.mailing_address,
    mailing_address_2 = EXCLUDED.mailing_address_2,
    mailing_city = EXCLUDED.mailing_city,
    mailing_state = EXCLUDED.mailing_state,
    mailing_zip = EXCLUDED.mailing_zip,
    situs_address = EXCLUDED.situs_address,
    situs_city = EXCLUDED.situs_city,
    situs_state = EXCLUDED.situs_state,
    situs_zip = EXCLUDED.situs_zip,
    map_id = EXCLUDED.map_id,
    legal_desc = EXCLUDED.legal_desc,
    tract_lot = EXCLUDED.tract_lot,
    block = EXCLUDED.block,
    add_legal = EXCLUDED.add_legal,
    township_range = EXCLUDED.township_range,
    assessor_map = EXCLUDED.assessor_map,
    loc_code = EXCLUDED.loc_code,
    account_status = EXCLUDED.account_status,
    levy_code = EXCLUDED.levy_code,
    nbo_code = EXCLUDED.nbo_code,
    imp_count = EXCLUDED.imp_count,
    prop_class = EXCLUDED.prop_class,
    prop_code = EXCLUDED.prop_code,
    zone_code = EXCLUDED.zone_code,
    deed_type = EXCLUDED.deed_type,
    deed_date = EXCLUDED.deed_date,
    inst_num = EXCLUDED.inst_num,
    sale_price = EXCLUDED.sale_price,
    sale_date = EXCLUDED.sale_date,
    exemption = EXCLUDED.exemption,
    size_acres = EXCLUDED.size_acres,
    size_sqft = EXCLUDED.size_sqft,
    imp_type = EXCLUDED.imp_type,
    act_year_built = EXCLUDED.act_year_built,
    main_area = EXCLUDED.main_area,
    units = EXCLUDED.units,
    main_sqft = EXCLUDED.main_sqft,
    roll_year = EXCLUDED.roll_year,
    roll_land = EXCLUDED.roll_land,
    roll_imp = EXCLUDED.roll_imp,
    roll_m50 = EXCLUDED.roll_m50,
    shape_length = EXCLUDED.shape_length,
    shape_area = EXCLUDED.shape_area,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    ingested_at = NOW();
"""


def fetch_page(offset: int) -> dict:
    url = (
        f"{SERVICE_URL}?where=1%3D1"
        f"&outFields=*&outSR=4326&f=geojson"
        f"&resultOffset={offset}&resultRecordCount={PAGE_SIZE}"
        f"&orderByFields=OBJECTID_1"
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
    """ArcGIS dates come as epoch milliseconds (int). Convert to ISO 8601 UTC for Postgres."""
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
    objectid = to_int(props.get("OBJECTID_1"))
    taxlot = to_str(props.get("MAPTAXLOT"))
    if objectid is None or taxlot is None:
        return None
    return (
        objectid,
        taxlot,
        to_str(props.get("PROPID")),
        to_str(props.get("ALTACCTNUM")),
        to_str(props.get("NAME")),
        to_str(props.get("NAME2")),
        to_str(props.get("ADDR1")),
        to_str(props.get("ADDR2")),
        to_str(props.get("CITY")),
        to_str(props.get("STATE")),
        to_str(props.get("ZIP")),
        to_str(props.get("SITUSADDR")),
        to_str(props.get("SITUSCITY")),
        to_str(props.get("SITUSSTATE")),
        to_str(props.get("SITUSZIP")),
        to_str(props.get("MAPID")),
        to_str(props.get("LEGAL")),
        to_str(props.get("TRACTLOT")),
        to_str(props.get("BLOCK")),
        to_str(props.get("ADDLEGAL")),
        to_str(props.get("TownshipRange")),
        to_str(props.get("AssessorMap")),
        to_str(props.get("LOC_CODE")),
        to_str(props.get("ACCOUNT_STATUS")),
        to_str(props.get("LEVYCODE")),
        to_str(props.get("NBOCODE")),
        to_str(props.get("IMP_COUNT")),
        to_str(props.get("PROPCLASS")),
        to_str(props.get("PROP_CODE")),
        to_str(props.get("ZONING")),
        to_str(props.get("DEED_TYPE")),
        to_ts_ms(props.get("DEED_DATE")),
        to_str(props.get("INST_NUM")),
        to_float(props.get("SALE_PRICE")),
        to_ts_ms(props.get("SALE_DATE")),
        to_str(props.get("EXEMPTION")),
        to_float(props.get("SIZEACRES")),
        to_int(props.get("SIZESQFT")),
        to_str(props.get("IMPTYPE")),
        to_int(props.get("ACTYEARBUILT")),
        to_int(props.get("MAINAREA")),
        to_int(props.get("UNITS")),
        to_int(props.get("MAIN_SQFT")),
        to_int(props.get("ROLLYEAR")),
        to_float(props.get("ROLLLAND")),
        to_float(props.get("ROLLIMP")),
        to_float(props.get("ROLLM50")),
        to_float(props.get("Shape__Length")),
        to_float(props.get("Shape__Area")),
        json.dumps(geom),
        json.dumps(props),
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
