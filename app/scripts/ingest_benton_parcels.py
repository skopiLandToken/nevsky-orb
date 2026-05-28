"""
Ingest Benton County taxlot+owner rows into parcels_benton.

Source: gis.co.benton.or.us/arcgis/rest/services/Public/TaxlotOwners/FeatureServer/0
        (Benton County ArcGIS Server 10.71, public anonymous, paginated.)

Strategy: paginated REST query with outSR=4326 + f=geojson so the server
          reprojects from EPSG:2913 (Oregon State Plane N) → WGS84. Single
          Polygon geometry from source → ST_Multi() on write to match
          parcels_marion / parcels_lane.

Pagination: 10.71 supports resultOffset / resultRecordCount. maxRecordCount is
            1000, so PAGE_SIZE=1000. Pages ordered by OBJECTID for deterministic
            offsets.

Owner explosion: TaxlotOwners returns one row per (parcel × owner party). Same
            polygon may appear 1..N times with different Party_Name. We INGEST
            ALL ROWS (each with its own OBJECTID PK + identical geom). The
            COUNTY_REGISTRY normalized lookup in orb_db.py aggregates with
            STRING_AGG(Party_Name) GROUP BY ORTaxlot to produce one logical
            parcel row at query time.

Idempotent: ON CONFLICT (objectid) DO UPDATE refreshes existing rows so this
            can serve as the daily-delta job (no LASTUPDATE field exists on
            this layer — full re-ingest is the only refresh path until a
            change-detection mechanism is added).

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_benton_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

SERVICE_URL = (
    "https://gis.co.benton.or.us/arcgis/rest/services/"
    "Public/TaxlotOwners/FeatureServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 1000               # maxRecordCount from layer metadata
BATCH_SIZE = 500
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


INSERT_SQL = """
INSERT INTO parcels_benton (
    objectid, county_fips,
    account_num, maptaxlot, ortaxlot, tax_code_area,
    situs_addr1, situs_city, situs_state, situs_zip,
    party_name, in_care_of,
    mail_line1, mail_line2, mail_city, mail_state, mail_zip,
    map_number,
    geom, raw_properties
) VALUES (
    %s, '003',
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s,
    %s, %s, %s, %s, %s,
    %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    account_num = EXCLUDED.account_num,
    maptaxlot = EXCLUDED.maptaxlot,
    ortaxlot = EXCLUDED.ortaxlot,
    tax_code_area = EXCLUDED.tax_code_area,
    situs_addr1 = EXCLUDED.situs_addr1,
    situs_city = EXCLUDED.situs_city,
    situs_state = EXCLUDED.situs_state,
    situs_zip = EXCLUDED.situs_zip,
    party_name = EXCLUDED.party_name,
    in_care_of = EXCLUDED.in_care_of,
    mail_line1 = EXCLUDED.mail_line1,
    mail_line2 = EXCLUDED.mail_line2,
    mail_city = EXCLUDED.mail_city,
    mail_state = EXCLUDED.mail_state,
    mail_zip = EXCLUDED.mail_zip,
    map_number = EXCLUDED.map_number,
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
        except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as e:
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
    ortaxlot = to_str(props.get("ORTaxlot"))
    if objectid is None or ortaxlot is None:
        return None
    return (
        objectid,
        to_str(props.get("Account_Num")),
        to_str(props.get("MapTaxlot")),
        ortaxlot,
        to_str(props.get("Tax_Code_Area")),
        to_str(props.get("Situs_Addr1")),
        to_str(props.get("Situs_City")),
        to_str(props.get("Situs_State")),
        to_str(props.get("Situs_Zip")),
        to_str(props.get("Party_Name")),
        to_str(props.get("In_Care_Of")),
        to_str(props.get("Mail_Line1")),
        to_str(props.get("Mail_Line2")),
        to_str(props.get("Mail_City")),
        to_str(props.get("Mail_State")),
        to_str(props.get("Mail_Zip")),
        to_str(props.get("MapNumber")),
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
    print(f"\nDONE: {total} rows ingested, {skipped} skipped, {elapsed:.1f}s")


if __name__ == "__main__":
    main()
