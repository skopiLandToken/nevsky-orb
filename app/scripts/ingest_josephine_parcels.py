"""
Ingest Josephine County (FIPS 033) taxlot parcels into parcels_josephine.

Source:
  gis.co.josephine.or.us/arcgis/rest/services/Assessor/Assessor_Taxlots/FeatureServer/0
  (on-prem ArcGIS Server, 41,974 polygons as of 2026-05-28). Hosted mirror at
  services3.arcgis.com/qwqIu50nUr6wRrbz/.../JoCo_Taxlot is ~200 features behind so
  we pull from on-prem.

Strategy:
  Paginated REST query with outSR=4326 so the server reprojects from Web Mercator
  (102100) to WGS84 for us. Idempotent: ON CONFLICT (objectid) DO UPDATE refreshes
  existing rows.

  Josephine's Assessor_Taxlots layer ships the richest inline attribute surface
  in TERRA: ownership + situs + valuation + zoning + most-recent recorded sale
  (DEED_TYPE, INST_NO, SALE_DATE, SALE_PRICE) all inline. fetch_josephine_assessment
  AND fetch_josephine_records become SQL pass-throughs over L1 — no scraper.

Memory: holds at most BATCH_SIZE features in RAM at any moment.

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_josephine_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

TAXLOT_URL = (
    "https://gis.co.josephine.or.us/arcgis/rest/services/"
    "Assessor/Assessor_Taxlots/FeatureServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 1000   # server maxRecordCount is 2000; 1000 is the safe TERRA-standard page
BATCH_SIZE = 500
HTTP_TIMEOUT = 90
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


PARCEL_INSERT_SQL = """
INSERT INTO parcels_josephine (
    objectid, taxlot, county_fips,
    map_num, mnx, sd, twn, rng, sec, qq, taxlot_num, type, loc_desc,
    lot, lot_1, block,
    account, acct_status,
    owner_name,
    mailing_address, mailing_addr1, mailing_addr2, mailing_addr3,
    mailing_city, mailing_state, mailing_zip, mailing_csz,
    situs_address, situs_city, situs_state, situs_zip,
    st_no, situs_pref, st_name, situs_suff, situs_suf0,
    appr_value, assd_value, imp_value, land_appr, land_mkt, rmv, mh_value,
    acreage, legal_acre, gis_acres,
    prop_class, bldg_class, code, maint, nbhd, sptb_codes, exempt, taxes,
    yr_blt, sq_ft, living_area, bedrms, mh_make, comp_mtl,
    zone,
    sale_date, sale_price, deed_type, inst_no, sale_type,
    asr_latitude, asr_longitude,
    shape_area, shape_length,
    geom, raw_properties
) VALUES (
    %s, %s, '033',
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s,
    %s, %s, %s, %s, %s,
    %s, %s,
    %s, %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    map_num = EXCLUDED.map_num,
    mnx = EXCLUDED.mnx,
    sd = EXCLUDED.sd,
    twn = EXCLUDED.twn,
    rng = EXCLUDED.rng,
    sec = EXCLUDED.sec,
    qq = EXCLUDED.qq,
    taxlot_num = EXCLUDED.taxlot_num,
    type = EXCLUDED.type,
    loc_desc = EXCLUDED.loc_desc,
    lot = EXCLUDED.lot,
    lot_1 = EXCLUDED.lot_1,
    block = EXCLUDED.block,
    account = EXCLUDED.account,
    acct_status = EXCLUDED.acct_status,
    owner_name = EXCLUDED.owner_name,
    mailing_address = EXCLUDED.mailing_address,
    mailing_addr1 = EXCLUDED.mailing_addr1,
    mailing_addr2 = EXCLUDED.mailing_addr2,
    mailing_addr3 = EXCLUDED.mailing_addr3,
    mailing_city = EXCLUDED.mailing_city,
    mailing_state = EXCLUDED.mailing_state,
    mailing_zip = EXCLUDED.mailing_zip,
    mailing_csz = EXCLUDED.mailing_csz,
    situs_address = EXCLUDED.situs_address,
    situs_city = EXCLUDED.situs_city,
    situs_state = EXCLUDED.situs_state,
    situs_zip = EXCLUDED.situs_zip,
    st_no = EXCLUDED.st_no,
    situs_pref = EXCLUDED.situs_pref,
    st_name = EXCLUDED.st_name,
    situs_suff = EXCLUDED.situs_suff,
    situs_suf0 = EXCLUDED.situs_suf0,
    appr_value = EXCLUDED.appr_value,
    assd_value = EXCLUDED.assd_value,
    imp_value = EXCLUDED.imp_value,
    land_appr = EXCLUDED.land_appr,
    land_mkt = EXCLUDED.land_mkt,
    rmv = EXCLUDED.rmv,
    mh_value = EXCLUDED.mh_value,
    acreage = EXCLUDED.acreage,
    legal_acre = EXCLUDED.legal_acre,
    gis_acres = EXCLUDED.gis_acres,
    prop_class = EXCLUDED.prop_class,
    bldg_class = EXCLUDED.bldg_class,
    code = EXCLUDED.code,
    maint = EXCLUDED.maint,
    nbhd = EXCLUDED.nbhd,
    sptb_codes = EXCLUDED.sptb_codes,
    exempt = EXCLUDED.exempt,
    taxes = EXCLUDED.taxes,
    yr_blt = EXCLUDED.yr_blt,
    sq_ft = EXCLUDED.sq_ft,
    living_area = EXCLUDED.living_area,
    bedrms = EXCLUDED.bedrms,
    mh_make = EXCLUDED.mh_make,
    comp_mtl = EXCLUDED.comp_mtl,
    zone = EXCLUDED.zone,
    sale_date = EXCLUDED.sale_date,
    sale_price = EXCLUDED.sale_price,
    deed_type = EXCLUDED.deed_type,
    inst_no = EXCLUDED.inst_no,
    sale_type = EXCLUDED.sale_type,
    asr_latitude = EXCLUDED.asr_latitude,
    asr_longitude = EXCLUDED.asr_longitude,
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
    # taxlot uses MapNum (14-char compound). Fall back to MNX if MapNum missing.
    taxlot = to_str(props.get("MapNum")) or to_str(props.get("MNX"))
    if objectid is None or taxlot is None:
        return None
    from datetime import datetime as _dt, timezone as _tz
    sale_date = props.get("SALE_DATE")
    sale_date_dt = None
    if isinstance(sale_date, (int, float)) and sale_date:
        try:
            sale_date_dt = _dt.fromtimestamp(sale_date / 1000.0, tz=_tz.utc)
        except (OverflowError, OSError, ValueError):
            sale_date_dt = None
    return (
        objectid,
        taxlot,
        to_str(props.get("MapNum")),
        to_str(props.get("MNX")),
        to_str(props.get("SD")),
        to_str(props.get("TWN")),
        to_str(props.get("RNG")),
        to_str(props.get("SEC")),
        to_str(props.get("QQ")),
        to_str(props.get("TAXLOT")),
        to_str(props.get("TYPE")),
        to_str(props.get("LOC_DESC")),
        to_str(props.get("LOT")),
        to_str(props.get("Lot_1")),
        to_str(props.get("BLOCK")),
        to_str(props.get("ACCOUNT")),
        to_str(props.get("ACCTSTATUS")),
        to_str(props.get("NAME")),
        to_str(props.get("ADDRESS")),
        to_str(props.get("ADDR1")),
        to_str(props.get("ADDR2")),
        to_str(props.get("ADDR3")),
        to_str(props.get("City")),
        to_str(props.get("State")),
        to_str(props.get("ZIP")),
        to_str(props.get("CSZ")),
        to_str(props.get("SITUS")),
        to_str(props.get("SITUS_CITY")),
        to_str(props.get("SITUS_ST")),
        to_str(props.get("SITUS_ZIP")),
        to_str(props.get("ST_NO")),
        to_str(props.get("SITUS_PREF")),
        to_str(props.get("ST_NAME")),
        to_str(props.get("SITUS_SUFF")),
        to_int(props.get("SITUS_SUF0")),
        to_float(props.get("APPR_VALUE")),
        to_float(props.get("ASSD_VALUE")),
        to_float(props.get("IMP_VALUE")),
        to_float(props.get("LAND_APPR")),
        to_float(props.get("LAND_MKT")),
        to_float(props.get("RMV")),
        to_float(props.get("MH_VALUE")),
        to_float(props.get("ACREAGE")),
        to_float(props.get("LEGAL_ACRE")),
        to_float(props.get("GIS_Acres")),
        to_str(props.get("PROP_CLASS")),
        to_str(props.get("BLDG_CLASS")),
        to_str(props.get("CODE")),
        to_str(props.get("MAINT")),
        to_str(props.get("NBHD")),
        to_str(props.get("SPTB_CODES")),
        to_int(props.get("Exempt")),
        to_float(props.get("Taxes")),
        to_float(props.get("YR_BLT")),
        to_float(props.get("SQ_FT")),
        to_float(props.get("LIVING_AREA")),
        to_float(props.get("BEDRMS")),
        to_str(props.get("MH_MAKE")),
        to_int(props.get("COMP_MTL")),
        to_str(props.get("Zone")),
        sale_date_dt,
        to_float(props.get("SALE_PRICE")),
        to_str(props.get("DEED_TYPE")),
        to_str(props.get("INST_NO")),
        to_str(props.get("SALE_TYPE")),
        to_float(props.get("Latitude")),
        to_float(props.get("Longitude")),
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
        parcels = ingest(conn, TAXLOT_URL, PARCEL_INSERT_SQL, parcel_row, "parcels_josephine")
        print(f"\nALL DONE: parcels={parcels}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
