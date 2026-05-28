"""
Ingest Douglas County taxlot parcels into parcels_douglas + Douglas zoning into
douglas_zoning, in a single run.

Sources:
  Parcels — gis.co.douglas.or.us/server/rest/services/Parcel/Parcels/FeatureServer/0
            (Douglas County ArcGIS Server 11.5, AGOL org "DCOR" / dcor.maps.arcgis.com).
            68,973 polygons as of 2026-05-28 with rich Marion-style inline attribute
            join (owner / mailing / situs / valuation / sale date / instrument # /
            propclass / acreage / legal description / specinterest).
  Zoning  — gis.co.douglas.or.us/server/rest/services/Planning/Zoning/FeatureServer/0
            (2302 polygons, DC_ZONE 16-char only). F1/F2/F3/FF/FG = forest variants
            (timber-MP differentiator); AW = agricultural watershed (EFU equiv);
            1R/5R/R1/R2/R3/RR = residential overlays; C1/C2/C3 commercial;
            M1/M2/M3 industrial.

Strategy: paginated REST query with outSR=4326 + f=geojson so the server reprojects
          from EPSG:2270 (Oregon SP South NAD83 HARN ftUS) to WGS84 for us. Single
          Polygon geometry from source → ST_Multi() on write so geom is uniformly
          MultiPolygon(4326) across the county fleet.

Pagination: server maxRecordCount per layer metadata is 50000 (parcels) / 2000
            (zoning), but ArcGIS practical standardMaxRecordCount is 4000. Use 2000
            to stay safely under the cliff. Pages ordered by OBJECTID for
            deterministic offsets.

Idempotent: ON CONFLICT (objectid) DO UPDATE refreshes existing rows so this can
            also serve as the daily-delta job (full re-ingest since source has no
            LASTUPDATE field).

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_douglas_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

PARCEL_URL = (
    "https://gis.co.douglas.or.us/server/rest/services/"
    "Parcel/Parcels/FeatureServer/0/query"
)
ZONING_URL = (
    "https://gis.co.douglas.or.us/server/rest/services/"
    "Planning/Zoning/FeatureServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 2000
BATCH_SIZE = 500
HTTP_TIMEOUT = 90
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


PARCEL_INSERT_SQL = """
INSERT INTO parcels_douglas (
    objectid, county_fips,
    taxid, prop_id, alt_acctnum, owner_id,
    owner_name,
    mail_addr1, mail_addr2, mail_addr3, mail_csz,
    situs_address, situs_csz,
    propclass, specinterest, codearea, loc_code, maintarea, nbhdcode,
    block, lot,
    assd_value, land_mkt_value, imprv_value, market_value,
    acct_acreage, mtl_acreage, total_acreage,
    inst_no, sale_date,
    legal,
    geom, raw_properties
) VALUES (
    %s, '019',
    %s, %s, %s, %s,
    %s,
    %s, %s, %s, %s,
    %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxid = EXCLUDED.taxid,
    prop_id = EXCLUDED.prop_id,
    alt_acctnum = EXCLUDED.alt_acctnum,
    owner_id = EXCLUDED.owner_id,
    owner_name = EXCLUDED.owner_name,
    mail_addr1 = EXCLUDED.mail_addr1,
    mail_addr2 = EXCLUDED.mail_addr2,
    mail_addr3 = EXCLUDED.mail_addr3,
    mail_csz = EXCLUDED.mail_csz,
    situs_address = EXCLUDED.situs_address,
    situs_csz = EXCLUDED.situs_csz,
    propclass = EXCLUDED.propclass,
    specinterest = EXCLUDED.specinterest,
    codearea = EXCLUDED.codearea,
    loc_code = EXCLUDED.loc_code,
    maintarea = EXCLUDED.maintarea,
    nbhdcode = EXCLUDED.nbhdcode,
    block = EXCLUDED.block,
    lot = EXCLUDED.lot,
    assd_value = EXCLUDED.assd_value,
    land_mkt_value = EXCLUDED.land_mkt_value,
    imprv_value = EXCLUDED.imprv_value,
    market_value = EXCLUDED.market_value,
    acct_acreage = EXCLUDED.acct_acreage,
    mtl_acreage = EXCLUDED.mtl_acreage,
    total_acreage = EXCLUDED.total_acreage,
    inst_no = EXCLUDED.inst_no,
    sale_date = EXCLUDED.sale_date,
    legal = EXCLUDED.legal,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    ingested_at = NOW();
"""


ZONING_INSERT_SQL = """
INSERT INTO douglas_zoning (
    objectid, zone_code, src_id, geom, raw_properties
) VALUES (
    %s, %s, %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    zone_code = EXCLUDED.zone_code,
    src_id = EXCLUDED.src_id,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    ingested_at = NOW();
"""


def fetch_page(base_url: str, offset: int, page_size: int) -> dict:
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
    taxid = to_str(props.get("TAXID"))
    if objectid is None or taxid is None:
        return None
    return (
        objectid,
        taxid,
        to_str(props.get("PROP_ID")),
        to_str(props.get("ALT_ACCTNUM")),
        to_int(props.get("OWNER_ID")),
        to_str(props.get("NAME")),
        to_str(props.get("ADDR1")),
        to_str(props.get("ADDR2")),
        to_str(props.get("ADDR3")),
        to_str(props.get("CSZ")),
        to_str(props.get("SitusAddress")),
        to_str(props.get("SitusCSZ")),
        to_str(props.get("PROPCLASS")),
        to_str(props.get("SPECINTEREST")),
        to_str(props.get("CODEAREA")),
        to_str(props.get("LOC_CODE")),
        to_str(props.get("MAINTAREA")),
        to_str(props.get("NBHDCODE")),
        to_str(props.get("BLOCK")),
        to_str(props.get("LOT")),
        to_float(props.get("ASSD_VALUE")),
        to_float(props.get("LAND_MKT_VALUE")),
        to_float(props.get("IMPRV_VALUE")),
        to_float(props.get("MARKET_VALUE")),
        to_float(props.get("ACCT_ACREAGE")),
        to_float(props.get("MTLACREAGE")),
        to_float(props.get("TotalAcreage")),
        to_str(props.get("INST_NO")),
        to_str(props.get("SaleDate")),
        to_str(props.get("LEGAL")),
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
    return (
        objectid,
        to_str(props.get("DC_ZONE")),
        to_int(props.get("ID")),
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
        print(
            f"[{label}] page {page_num} offset={offset} got {len(feats)} | "
            f"total: {total} | skipped: {skipped} | elapsed: {elapsed:.1f}s",
            flush=True,
        )

        if len(feats) < PAGE_SIZE and not page.get("exceededTransferLimit"):
            break
        offset += PAGE_SIZE

    elapsed = time.time() - t0
    print(f"[{label}] DONE: {total} ingested, {skipped} skipped, {elapsed:.1f}s")
    return total


def main():
    conn = psycopg.connect(DB_DSN)
    conn.autocommit = False
    try:
        parcels = ingest(conn, PARCEL_URL, PARCEL_INSERT_SQL, parcel_row, "parcels_douglas")
        zoning = ingest(conn, ZONING_URL, ZONING_INSERT_SQL, zoning_row, "douglas_zoning")
        print(f"\nALL DONE: parcels={parcels}, zoning_polys={zoning}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
