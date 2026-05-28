"""
Ingest Clatsop County taxlot parcels into parcels_clatsop.

Source: https://delta.co.clatsop.or.us/server/rest/services/Taxlots/FeatureServer/1
        (Clatsop County's own ArcGIS Server — instance path is `/server`, NOT
        `/arcgis`. Query capability confirmed; 35,456 parcels; maxRecordCount 5000.
        The ODF statewide TaxlotsDisplay MapServer has query DISABLED so the
        county-direct FeatureServer is the canonical L1 source.)

Strategy:
  Paginated REST query with outSR=4326 + f=geojson so the server reprojects to
  WGS84. Idempotent: ON CONFLICT (objectid) DO UPDATE refreshes existing rows.
  Server maxRecordCount = 5000.

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_clatsop_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

PARCEL_URL = (
    "https://delta.co.clatsop.or.us/server/rest/services/"
    "Taxlots/FeatureServer/1/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 5000   # server maxRecordCount for this FeatureServer layer
BATCH_SIZE = 1000
HTTP_TIMEOUT = 90
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


PARCEL_INSERT_SQL = """
INSERT INTO parcels_clatsop (
    objectid,
    taxlot, ortaxlot, taxlot_short, taxmapnum, taxmapkey, taxlotkey, map_number, si_map_tax,
    account_id,
    owner_line, owner_ll_1, owner_ll_2, in_care_of,
    street_add, optional_a, po_box, unit_number, mail_city, mail_state, mail_zip,
    situs_address, situs_city,
    property_class, stat_class, year_built, tax_code, ma, nh, septic_status, own_sit_lot, labeling,
    shape_area,
    geom, raw_properties
) VALUES (
    %s,
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s,
    ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    ortaxlot = EXCLUDED.ortaxlot,
    taxlot_short = EXCLUDED.taxlot_short,
    taxmapnum = EXCLUDED.taxmapnum,
    taxmapkey = EXCLUDED.taxmapkey,
    taxlotkey = EXCLUDED.taxlotkey,
    map_number = EXCLUDED.map_number,
    si_map_tax = EXCLUDED.si_map_tax,
    account_id = EXCLUDED.account_id,
    owner_line = EXCLUDED.owner_line,
    owner_ll_1 = EXCLUDED.owner_ll_1,
    owner_ll_2 = EXCLUDED.owner_ll_2,
    in_care_of = EXCLUDED.in_care_of,
    street_add = EXCLUDED.street_add,
    optional_a = EXCLUDED.optional_a,
    po_box = EXCLUDED.po_box,
    unit_number = EXCLUDED.unit_number,
    mail_city = EXCLUDED.mail_city,
    mail_state = EXCLUDED.mail_state,
    mail_zip = EXCLUDED.mail_zip,
    situs_address = EXCLUDED.situs_address,
    situs_city = EXCLUDED.situs_city,
    property_class = EXCLUDED.property_class,
    stat_class = EXCLUDED.stat_class,
    year_built = EXCLUDED.year_built,
    tax_code = EXCLUDED.tax_code,
    ma = EXCLUDED.ma,
    nh = EXCLUDED.nh,
    septic_status = EXCLUDED.septic_status,
    own_sit_lot = EXCLUDED.own_sit_lot,
    labeling = EXCLUDED.labeling,
    shape_area = EXCLUDED.shape_area,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    ingested_at = NOW();
"""


def fetch_page(offset: int) -> dict:
    url = (
        f"{PARCEL_URL}?where=1%3D1"
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
        try:
            return int(float(v))
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
    taxlot = to_str(props.get("ORTaxlot"))
    if objectid is None or taxlot is None:
        return None
    return (
        objectid,
        # identifiers
        taxlot,
        to_str(props.get("ORTaxlot")),
        to_str(props.get("Taxlot")),
        to_str(props.get("TAXMAPNUM")),
        to_str(props.get("TAXMAPKEY")),
        to_str(props.get("TAXLOTKEY")),
        to_str(props.get("MAPNUM")),
        to_str(props.get("SIMapTax")),
        # account
        to_int(props.get("ACCOUNT_ID")),
        # ownership
        to_str(props.get("OWNER_LINE")),
        to_str(props.get("OWNER_LL_1")),
        to_str(props.get("OWNER_LL_2")),
        to_str(props.get("IN_CARE_OF")),
        # mailing
        to_str(props.get("STREET_ADD")),
        to_str(props.get("OPTIONAL_A")),
        to_str(props.get("PO_BOX")),
        to_str(props.get("UNIT_NUMBE")),
        to_str(props.get("CITY")),
        to_str(props.get("STATE")),
        to_str(props.get("ZIP_CODE")),
        # situs
        to_str(props.get("SITUS_ADDR")),
        to_str(props.get("SITUS_CITY")),
        # classification / building
        to_str(props.get("PROPERTY_C")),
        to_str(props.get("STAT_CLASS")),
        to_int(props.get("YEAR_BUILT")),
        to_str(props.get("TAXCODE")),
        to_str(props.get("MA")),
        to_str(props.get("NH")),
        to_int(props.get("SEPTIC_STATUS")),
        to_str(props.get("OwnSitLot")),
        to_str(props.get("Labeling")),
        # area
        to_float(props.get("Shape__Area")),
        # geom + raw
        json.dumps(geom),
        json.dumps(props),
    )


def ingest(conn) -> int:
    t0 = time.time()
    total = 0
    skipped = 0
    offset = 0
    page_num = 0
    while True:
        page_num += 1
        page = fetch_page(offset)
        feats = page.get("features", []) or []
        if not feats:
            break

        batch = []
        for f in feats:
            row = parcel_row(f)
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                with conn.cursor() as cur:
                    cur.executemany(PARCEL_INSERT_SQL, batch)
                conn.commit()
                total += len(batch)
                batch = []

        if batch:
            with conn.cursor() as cur:
                cur.executemany(PARCEL_INSERT_SQL, batch)
            conn.commit()
            total += len(batch)

        elapsed = time.time() - t0
        print(f"[parcels_clatsop] page {page_num} offset={offset} got {len(feats)} | total: {total} | skipped: {skipped} | elapsed: {elapsed:.1f}s")

        if len(feats) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    elapsed = time.time() - t0
    print(f"[parcels_clatsop] DONE: {total} ingested, {skipped} skipped, {elapsed:.1f}s")
    return total


def main():
    conn = psycopg.connect(DB_DSN)
    conn.autocommit = False
    try:
        parcels = ingest(conn)
        print(f"\nALL DONE: parcels={parcels}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
