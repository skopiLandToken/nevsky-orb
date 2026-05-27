"""
Ingest Clackamas County taxlot parcels into parcels_clackamas.

Source: services3.arcgis.com/I2eWXOndpF9m8oKC/arcgis/rest/services/Taxlots/FeatureServer/0
        (Clackamas County GIS — hosted feature layer view. Updated weekly.)

Strategy: paginated REST query with outSR=4326 + f=geojson so the server reprojects
          Web Mercator (102100/3857) → WGS84. Single Polygon geometry from source
          → cast ST_Multi() on write to match every other parcels_* table.

Pagination: hosted FeatureServer supports resultOffset / resultRecordCount. Use
            PAGE_SIZE=2000 (well under the 2000-default max for hosted layers).
            Pages ordered by OBJECTID for deterministic offsets.

Idempotent: ON CONFLICT (objectid) DO UPDATE refreshes existing rows so this can
            also serve as the weekly delta job — the source updates weekly per
            the HFLV description and exposes editingInfo.lastEditDate on the
            layer metadata for future incremental-only refreshes.

Memory: at most BATCH_SIZE features in RAM at any moment.

recordCount at authoring time: 163,585 parcels → 82 pages at 2000/page.

Schema note: Clackamas's hosted view is THIN — only TLNO/PARCEL_NUMBER/SITUS/
            SITUS_CITY/SITUS_ZIP/TAXCODE + geom. No inline owner/valuation/sale/
            zoning (stripped from public view by source per their description).
            Layer 2 fetchers do the heavy work on demand.

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_clackamas_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

SERVICE_URL = (
    "https://services3.arcgis.com/I2eWXOndpF9m8oKC/arcgis/rest/services/"
    "Taxlots/FeatureServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 2000
BATCH_SIZE = 500
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


INSERT_SQL = """
INSERT INTO parcels_clackamas (
    objectid, taxlot, county_fips,
    parcel_number, tax_code,
    situs_address, situs_city, situs_zip,
    shape_length, shape_area,
    geom, raw_properties
) VALUES (
    %s, %s, '005',
    %s, %s,
    %s, %s, %s,
    %s, %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    parcel_number = EXCLUDED.parcel_number,
    tax_code = EXCLUDED.tax_code,
    situs_address = EXCLUDED.situs_address,
    situs_city = EXCLUDED.situs_city,
    situs_zip = EXCLUDED.situs_zip,
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
    taxlot = to_str(props.get("TLNO"))
    if objectid is None or taxlot is None:
        return None
    return (
        objectid,
        taxlot,
        to_str(props.get("PARCEL_NUMBER")),
        to_str(props.get("TAXCODE")),
        to_str(props.get("SITUS")),
        to_str(props.get("SITUS_CITY")),
        to_int(props.get("SITUS_ZIP")),
        to_float(props.get("Shape__Length") or props.get("Shape_Length")),
        to_float(props.get("Shape__Area") or props.get("Shape_Area")),
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
