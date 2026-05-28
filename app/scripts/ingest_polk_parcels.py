"""
Ingest Polk County taxlot parcels into parcels_polk.

Source: maps.co.polk.or.us/gis/rest/services/Assessor/Taxlots/MapServer/11
        (Polk County ArcGIS Server 11.4; public, anonymous, paginated.
         NOTE: REST endpoint lives at /gis/rest, not /arcgis/rest.)

Strategy: paginated REST query with outSR=4326 + f=geojson so the server
          reprojects from Oregon Statewide Lambert (EPSG:2913) for us.
          Single Polygon geometry from source → ST_Multi() on write to match
          all other parcels_* tables.

Pagination: ArcGIS Server 11.4 supports resultOffset / resultRecordCount.
            maxRecordCount per layer metadata is 2000, so PAGE_SIZE=2000.
            Pages ordered by OBJECTID for deterministic offsets.

Idempotent: ON CONFLICT (objectid) DO UPDATE refreshes existing rows so this
            can also serve as the daily-delta job once a source-side modified-
            timestamp is identified (none in current schema — IN FLIGHT).

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_polk_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

SERVICE_URL = (
    "https://maps.co.polk.or.us/gis/rest/services/"
    "Assessor/Taxlots/MapServer/11/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 2000               # maxRecordCount from layer metadata
BATCH_SIZE = 500
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


INSERT_SQL = """
INSERT INTO parcels_polk (
    objectid, county_fips,
    taxlot, maptaxlot, ortaxlot, map_taxlot_short, map_number, ormapnum, special_int,
    account_id, prim_acc_num, si_map_tax,
    owner_line1, agent_name, in_care_of,
    mail_add1, mail_add2, mail_city, mail_state, mail_zip, mail_country,
    site_add_nam, site_add_cty, site_zip,
    inst_year, inst_month, inst_id, inst_type,
    dwelling, prp_class, prp_cls_desc,
    ast_imp_val, ast_lnd_val, ast_value,
    sa, ma, nh, tax_code, tax_code_area, unit_id,
    taxlot_acres, taxlot_feet, acct_acres, acct_sqft,
    shape_area, shape_length,
    geom, raw_properties, last_modified
) VALUES (
    %s, '053',
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb,
    %s
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    maptaxlot = EXCLUDED.maptaxlot,
    ortaxlot = EXCLUDED.ortaxlot,
    map_taxlot_short = EXCLUDED.map_taxlot_short,
    map_number = EXCLUDED.map_number,
    ormapnum = EXCLUDED.ormapnum,
    special_int = EXCLUDED.special_int,
    account_id = EXCLUDED.account_id,
    prim_acc_num = EXCLUDED.prim_acc_num,
    si_map_tax = EXCLUDED.si_map_tax,
    owner_line1 = EXCLUDED.owner_line1,
    agent_name = EXCLUDED.agent_name,
    in_care_of = EXCLUDED.in_care_of,
    mail_add1 = EXCLUDED.mail_add1,
    mail_add2 = EXCLUDED.mail_add2,
    mail_city = EXCLUDED.mail_city,
    mail_state = EXCLUDED.mail_state,
    mail_zip = EXCLUDED.mail_zip,
    mail_country = EXCLUDED.mail_country,
    site_add_nam = EXCLUDED.site_add_nam,
    site_add_cty = EXCLUDED.site_add_cty,
    site_zip = EXCLUDED.site_zip,
    inst_year = EXCLUDED.inst_year,
    inst_month = EXCLUDED.inst_month,
    inst_id = EXCLUDED.inst_id,
    inst_type = EXCLUDED.inst_type,
    dwelling = EXCLUDED.dwelling,
    prp_class = EXCLUDED.prp_class,
    prp_cls_desc = EXCLUDED.prp_cls_desc,
    ast_imp_val = EXCLUDED.ast_imp_val,
    ast_lnd_val = EXCLUDED.ast_lnd_val,
    ast_value = EXCLUDED.ast_value,
    sa = EXCLUDED.sa,
    ma = EXCLUDED.ma,
    nh = EXCLUDED.nh,
    tax_code = EXCLUDED.tax_code,
    tax_code_area = EXCLUDED.tax_code_area,
    unit_id = EXCLUDED.unit_id,
    taxlot_acres = EXCLUDED.taxlot_acres,
    taxlot_feet = EXCLUDED.taxlot_feet,
    acct_acres = EXCLUDED.acct_acres,
    acct_sqft = EXCLUDED.acct_sqft,
    shape_area = EXCLUDED.shape_area,
    shape_length = EXCLUDED.shape_length,
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


def feature_to_row(feature, ingest_ts: str):
    props = feature.get("properties", {}) or {}
    geom = feature.get("geometry")
    if geom is None:
        return None
    objectid = to_int(props.get("OBJECTID"))
    taxlot = to_str(props.get("TaxlotTemp_ORTaxlot"))
    if objectid is None or taxlot is None:
        return None
    return (
        objectid,
        taxlot,
        to_str(props.get("TaxlotTemp_MapTaxlot")),
        to_str(props.get("TaxlotTemp_ORTaxlot")),
        to_str(props.get("TaxlotTemp_TAXLOT")),
        to_str(props.get("TaxlotTemp_MapNumber")),
        to_str(props.get("TaxlotTemp_ORMapNum")),
        to_str(props.get("TaxlotTemp_SpecialInt")),
        to_int(props.get("AcctTable_Account_ID")),
        to_str(props.get("AcctTable_PrimAccNum")),
        to_str(props.get("AcctTable_SIMapTax")),
        to_str(props.get("AcctTable_OwnerLine1")),
        to_str(props.get("AcctTable_AgentName")),
        to_str(props.get("AcctTable_In_Care_Of")),
        to_str(props.get("AcctTable_MailAdd1")),
        to_str(props.get("AcctTable_MailAdd2")),
        to_str(props.get("AcctTable_MailCity")),
        to_str(props.get("AcctTable_MailState")),
        to_str(props.get("AcctTable_MailZip")),
        to_str(props.get("AcctTable_MailCntry")),
        to_str(props.get("AcctTable_SiteAddNam")),
        to_str(props.get("AcctTable_SiteAddCty")),
        to_str(props.get("AcctTable_SiteZip")),
        to_int(props.get("AcctTable_InstYear")),
        to_int(props.get("AcctTable_InstMonth")),
        to_str(props.get("AcctTable_InstId")),
        to_str(props.get("AcctTable_InstType")),
        to_str(props.get("AcctTable_Dwelling")),
        to_str(props.get("AcctTable_PrpClass")),
        to_str(props.get("AcctTable_PrpClsDsc")),
        to_int(props.get("AcctTable_AstImpVal")),
        to_int(props.get("AcctTable_AstLndVal")),
        to_int(props.get("AcctTable_AstValue")),
        to_str(props.get("AcctTable_SA")),
        to_str(props.get("AcctTable_MA")),
        to_str(props.get("AcctTable_NH")),
        to_str(props.get("AcctTable_TaxCode")),
        to_str(props.get("AcctTable_TaxCodeArea")),
        to_int(props.get("AcctTable_Unit_ID")),
        to_float(props.get("TaxlotTemp_TaxlotAcre")),
        to_int(props.get("TaxlotTemp_TaxlotFeet")),
        to_float(props.get("AcctTable_Acct_Acres")),
        to_str(props.get("AcctTable_Acct_SqFt")),
        to_float(props.get("Shape.STArea()")),
        to_float(props.get("Shape.STLength()")),
        json.dumps(geom),
        json.dumps(props),
        ingest_ts,
    )


def main():
    t0 = time.time()
    ingest_ts = time.strftime("%Y-%m-%d %H:%M:%S+00", time.gmtime())
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
                row = feature_to_row(f, ingest_ts)
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

            if len(feats) < PAGE_SIZE and not page.get("exceededTransferLimit"):
                break
            offset += PAGE_SIZE

    finally:
        conn.close()

    elapsed = time.time() - t0
    print(f"\nDONE: {total} parcels ingested, {skipped} skipped, {elapsed:.1f}s")


if __name__ == "__main__":
    main()
