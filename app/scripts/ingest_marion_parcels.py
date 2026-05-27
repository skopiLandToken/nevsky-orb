"""
Ingest Marion County taxlot parcels into parcels_marion.

Source: gis.co.marion.or.us/arcgis/rest/services/Public/Parcels/MapServer/0
        (Marion County ArcGIS Server 10.91; public, anonymous, paginated.)

Strategy: paginated REST query with outSR=4326 + f=geojson so the server
          reprojects from Web Mercator (102100) for us. Single Polygon geometry
          from source → ST_Multi() on write to match parcels_crook/multnomah.

Pagination: ArcGIS Server 10.91 supports resultOffset / resultRecordCount.
            maxRecordCount per layer metadata is 1000, so PAGE_SIZE=1000.
            Pages ordered by OBJECTID for deterministic offsets.

Idempotent: ON CONFLICT (objectid) DO UPDATE refreshes existing rows so this
            can also serve as the daily-delta job once LASTUPDATE-driven filter
            is wired (see KB_80 next-session items).

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_marion_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

SERVICE_URL = (
    "https://gis.co.marion.or.us/arcgis/rest/services/"
    "Public/Parcels/MapServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 1000               # maxRecordCount from layer metadata
BATCH_SIZE = 500
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


INSERT_SQL = """
INSERT INTO parcels_marion (
    objectid, taxlot, county_fips,
    taxacct, other_accts, alt_taxlot, alt_taxacct, taxlot_ns,
    street_num, pre_dir, street_name, street_type, post_dir, unit_num,
    situs_address, situs_csz,
    plat_name, block, lot,
    acres,
    inst_num, inst_type, inst_date, sale_price,
    owner_name, owner_addr, owner_csz,
    zone_code, zone_auth, zone_web,
    year_built, bldg_area, living_area,
    prop_class, prp_cls_desc,
    rmv_land, rmv_imp, rmv_total, assd_val,
    tax_code, city, school_dist, fire_dist,
    reflink, maplink,
    geom, raw_properties, last_modified
) VALUES (
    %s, %s, '047',
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s,
    %s, %s, %s,
    %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb,
    %s
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    taxacct = EXCLUDED.taxacct,
    other_accts = EXCLUDED.other_accts,
    alt_taxlot = EXCLUDED.alt_taxlot,
    alt_taxacct = EXCLUDED.alt_taxacct,
    taxlot_ns = EXCLUDED.taxlot_ns,
    street_num = EXCLUDED.street_num,
    pre_dir = EXCLUDED.pre_dir,
    street_name = EXCLUDED.street_name,
    street_type = EXCLUDED.street_type,
    post_dir = EXCLUDED.post_dir,
    unit_num = EXCLUDED.unit_num,
    situs_address = EXCLUDED.situs_address,
    situs_csz = EXCLUDED.situs_csz,
    plat_name = EXCLUDED.plat_name,
    block = EXCLUDED.block,
    lot = EXCLUDED.lot,
    acres = EXCLUDED.acres,
    inst_num = EXCLUDED.inst_num,
    inst_type = EXCLUDED.inst_type,
    inst_date = EXCLUDED.inst_date,
    sale_price = EXCLUDED.sale_price,
    owner_name = EXCLUDED.owner_name,
    owner_addr = EXCLUDED.owner_addr,
    owner_csz = EXCLUDED.owner_csz,
    zone_code = EXCLUDED.zone_code,
    zone_auth = EXCLUDED.zone_auth,
    zone_web = EXCLUDED.zone_web,
    year_built = EXCLUDED.year_built,
    bldg_area = EXCLUDED.bldg_area,
    living_area = EXCLUDED.living_area,
    prop_class = EXCLUDED.prop_class,
    prp_cls_desc = EXCLUDED.prp_cls_desc,
    rmv_land = EXCLUDED.rmv_land,
    rmv_imp = EXCLUDED.rmv_imp,
    rmv_total = EXCLUDED.rmv_total,
    assd_val = EXCLUDED.assd_val,
    tax_code = EXCLUDED.tax_code,
    city = EXCLUDED.city,
    school_dist = EXCLUDED.school_dist,
    fire_dist = EXCLUDED.fire_dist,
    reflink = EXCLUDED.reflink,
    maplink = EXCLUDED.maplink,
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


def to_ts_ms(v):
    """ArcGIS dates arrive as epoch milliseconds. Convert to ISO 8601 UTC for Postgres."""
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
    taxlot = to_str(props.get("TAXLOT"))
    if objectid is None or taxlot is None:
        return None
    return (
        objectid,
        taxlot,
        to_str(props.get("TAXACCT")),
        to_str(props.get("OTHERACCTS")),
        to_str(props.get("ALT_TAXLOT")),
        to_str(props.get("ALT_TAXACCT")),
        to_str(props.get("TAXLOTNS")),
        to_str(props.get("STREETNUM")),
        to_str(props.get("PRE_DIR")),
        to_str(props.get("STREETNAME")),
        to_str(props.get("STREETTYPE")),
        to_str(props.get("POST_DIR")),
        to_str(props.get("UNITNUM")),
        to_str(props.get("SITUS")),
        to_str(props.get("SITUSCSZ")),
        to_str(props.get("PLATNAME")),
        to_str(props.get("BLOCK")),
        to_str(props.get("LOT")),
        to_float(props.get("ACRES")),
        to_str(props.get("INSTNUM")),
        to_str(props.get("INSTTYPE")),
        to_ts_ms(props.get("INSTDATE")),
        to_int(props.get("SALEPRICE")),
        to_str(props.get("OWNERNAME")),
        to_str(props.get("OWNERADDR")),
        to_str(props.get("OWNERCSZ")),
        to_str(props.get("ZONECODE")),
        to_str(props.get("ZONEAUTH")),
        to_str(props.get("ZONEWEB")),
        to_int(props.get("YEARBUILT")),
        to_int(props.get("BLDGAREA")),
        to_int(props.get("LIVINGAREA")),
        to_str(props.get("PROPCLASS")),
        to_str(props.get("PRPCLSDESC")),
        to_int(props.get("RMVLND")),
        to_int(props.get("RMVIMP")),
        to_int(props.get("RMVTOTAL")),
        to_int(props.get("ASSDVAL")),
        to_str(props.get("TAXCODE")),
        to_str(props.get("CITY")),
        to_str(props.get("SCHLDIST")),
        to_str(props.get("FIREDIST")),
        to_str(props.get("REFLINK")),
        to_str(props.get("MAPLINK")),
        json.dumps(geom),
        json.dumps(props),
        to_ts_ms(props.get("LASTUPDATE")),
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
