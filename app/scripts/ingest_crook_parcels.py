"""
Ingest Crook County taxlot parcels into parcels_crook table.

Source: gis.crookcountyor.gov CC_Taxlots_Helion FeatureServer layer 0
        (Helion is Crook's records system; this is the canonical taxlot layer.)
Strategy: paginated REST query with outSR=4326 so the server reprojects
          from EPSG:2914 (Oregon North NAD83 HARN ftUS) to WGS84 for us.
Idempotent: ON CONFLICT (objectid) DO UPDATE re-runs refresh existing rows.

Memory: holds at most BATCH_SIZE features in RAM at any moment.

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_crook_parcels
"""
import os
import json
import time
import sys
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

SERVICE_URL = (
    "https://gis.crookcountyor.gov/server/rest/services/"
    "CC_Taxlots_Helion/FeatureServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 2000   # server max for this service
BATCH_SIZE = 500   # DB insert batch
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


INSERT_SQL = """
INSERT INTO parcels_crook (
    objectid, taxlot, county_fips,
    owner_name, situs_address, mailing_address,
    account, prop_class, pc_desc, tax_code_area,
    sub_name, sub_block, sub_lot,
    assessed_acres, gis_acres,
    zone_code, zone_desc,
    map_number, pats_url, tax_card_url, tax_map_url, pso_url, global_id,
    shape_length, shape_area,
    geom, raw_properties
) VALUES (
    %s, %s, '013',
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    owner_name = EXCLUDED.owner_name,
    situs_address = EXCLUDED.situs_address,
    mailing_address = EXCLUDED.mailing_address,
    account = EXCLUDED.account,
    prop_class = EXCLUDED.prop_class,
    pc_desc = EXCLUDED.pc_desc,
    tax_code_area = EXCLUDED.tax_code_area,
    sub_name = EXCLUDED.sub_name,
    sub_block = EXCLUDED.sub_block,
    sub_lot = EXCLUDED.sub_lot,
    assessed_acres = EXCLUDED.assessed_acres,
    gis_acres = EXCLUDED.gis_acres,
    zone_code = EXCLUDED.zone_code,
    zone_desc = EXCLUDED.zone_desc,
    map_number = EXCLUDED.map_number,
    pats_url = EXCLUDED.pats_url,
    tax_card_url = EXCLUDED.tax_card_url,
    tax_map_url = EXCLUDED.tax_map_url,
    pso_url = EXCLUDED.pso_url,
    global_id = EXCLUDED.global_id,
    shape_length = EXCLUDED.shape_length,
    shape_area = EXCLUDED.shape_area,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    ingested_at = NOW();
"""


def fetch_page(offset: int) -> dict:
    """Pull one page of features with retries on transient HTTP errors."""
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


def feature_to_row(feature):
    props = feature.get("properties", {}) or {}
    geom = feature.get("geometry")
    if geom is None:
        return None
    objectid = to_int(props.get("OBJECTID"))
    taxlot = to_str(props.get("MAPTAXLOT"))
    if objectid is None or taxlot is None:
        return None
    return (
        objectid,
        taxlot,
        to_str(props.get("OWNER_NAME")),
        to_str(props.get("SITUS_ADDRESS")),
        to_str(props.get("mail")),
        to_int(props.get("ACCOUNT")),
        to_str(props.get("PROP_CLASS")),
        to_str(props.get("PC_DESC")),
        to_str(props.get("TAX_CODE_AREA")),
        to_str(props.get("SUB_NAME")),
        to_str(props.get("SUB_BLOCK")),
        to_str(props.get("SUB_LOT")),
        to_float(props.get("SUM_TOT_ACRES")),
        to_float(props.get("GIS_ACRES")),
        to_str(props.get("ZONE")),
        to_str(props.get("ZONE_DESC")),
        to_str(props.get("MAP_NUMBER")),
        to_str(props.get("PATS_LINK")),
        to_str(props.get("TAX_CARD_LINK")),
        to_str(props.get("TAX_MAP_LINK")),
        to_str(props.get("PSO_Link")),
        to_str(props.get("GlobalID")),
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
            print(f"page {page_num} offset={offset} got {len(feats)} | total ingested: {total} | skipped: {skipped} | elapsed: {elapsed:.1f}s")

            # ArcGIS REST signals last page when fewer than PAGE_SIZE returned
            if len(feats) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

    finally:
        conn.close()

    elapsed = time.time() - t0
    print(f"\nDONE: {total} parcels ingested, {skipped} skipped, {elapsed:.1f}s")


if __name__ == "__main__":
    main()
