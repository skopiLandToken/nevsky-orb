"""
Ingest Linn County taxlot parcels into parcels_linn.

Source: gis.co.linn.or.us/arcgis/rest/services/AssessmentTax/ORMAP_public/MapServer/3
        (Linn County ArcGIS Server 11.5; AssessmentTax/ORMAP_public, Taxlot layer.)

Strategy: paginated REST query with outSR=4326 + f=geojson so the server
          reprojects from Oregon Lambert ft (EPSG:2913) to WGS84 for us. Single
          Polygon geometry from source → ST_Multi() on write to match the rest
          of the county fleet (MultiPolygon(4326)).

Pagination: ArcGIS Server 11.5 supports resultOffset / resultRecordCount.
            maxRecordCount per layer metadata is 2000, so PAGE_SIZE=2000.
            Pages ordered by OBJECTID for deterministic offsets.

Validation: source has ~917 polygons with `Taxlot IN ('ROADS','WATER','CITY')`
            kept for cartographic completeness; we ingest them all. ST_MakeValid
            on read repairs any invalid rings.

Idempotent: ON CONFLICT (objectid) DO UPDATE refreshes existing rows so this
            can also serve as the daily-delta job (filter on last_edited_date
            for incremental refresh once the daily-poll worker lands).

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_linn_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

SERVICE_URL = (
    "https://gis.co.linn.or.us/arcgis/rest/services/"
    "AssessmentTax/ORMAP_public/MapServer/3/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 2000               # maxRecordCount from layer metadata
BATCH_SIZE = 500
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


INSERT_SQL = """
INSERT INTO parcels_linn (
    objectid, taxlot, county_fips,
    map_taxlot, or_taxlot, map_number, or_map_num, record_name,
    taxlot_acre, map_acres, calculated_acres, stated_area, stated_area_unit,
    relia_code, source, source_type, validation_status,
    map_suf_type, map_class, map_rel_code, map_suf_num, is_seed,
    special_int, name, misclose_ratio, misclose_distance, county_code,
    created_user, created_date, last_edited_user, last_edited_date,
    auto_date, auto_method, auto_who, global_id,
    geom, raw_properties
) VALUES (
    %s, %s, '043',
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    ST_Multi(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    map_taxlot = EXCLUDED.map_taxlot,
    or_taxlot = EXCLUDED.or_taxlot,
    map_number = EXCLUDED.map_number,
    or_map_num = EXCLUDED.or_map_num,
    record_name = EXCLUDED.record_name,
    taxlot_acre = EXCLUDED.taxlot_acre,
    map_acres = EXCLUDED.map_acres,
    calculated_acres = EXCLUDED.calculated_acres,
    stated_area = EXCLUDED.stated_area,
    stated_area_unit = EXCLUDED.stated_area_unit,
    relia_code = EXCLUDED.relia_code,
    source = EXCLUDED.source,
    source_type = EXCLUDED.source_type,
    validation_status = EXCLUDED.validation_status,
    map_suf_type = EXCLUDED.map_suf_type,
    map_class = EXCLUDED.map_class,
    map_rel_code = EXCLUDED.map_rel_code,
    map_suf_num = EXCLUDED.map_suf_num,
    is_seed = EXCLUDED.is_seed,
    special_int = EXCLUDED.special_int,
    name = EXCLUDED.name,
    misclose_ratio = EXCLUDED.misclose_ratio,
    misclose_distance = EXCLUDED.misclose_distance,
    county_code = EXCLUDED.county_code,
    created_user = EXCLUDED.created_user,
    created_date = EXCLUDED.created_date,
    last_edited_user = EXCLUDED.last_edited_user,
    last_edited_date = EXCLUDED.last_edited_date,
    auto_date = EXCLUDED.auto_date,
    auto_method = EXCLUDED.auto_method,
    auto_who = EXCLUDED.auto_who,
    global_id = EXCLUDED.global_id,
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
    taxlot = to_str(props.get("Taxlot"))
    if objectid is None or taxlot is None:
        return None
    return (
        objectid,
        taxlot,
        to_str(props.get("MapTaxlot")),
        to_str(props.get("ORTaxlot")),
        to_str(props.get("MapNumber")),
        to_str(props.get("ORMapNum")),
        to_str(props.get("RecordName")),
        to_float(props.get("TaxlotAcre")),
        to_float(props.get("MapAcres")),
        to_float(props.get("CalculatedAcres")),
        to_float(props.get("StatedArea")),
        to_int(props.get("StatedAreaUnit")),
        to_int(props.get("ReliaCode")),
        to_str(props.get("Source")),
        to_str(props.get("SourceType")),
        to_int(props.get("VALIDATIONSTATUS")),
        to_str(props.get("MapSufType")),
        to_str(props.get("MapClass")),
        to_str(props.get("MapRelCode")),
        to_int(props.get("MapSufNum")),
        to_int(props.get("IsSeed")),
        to_str(props.get("SpecialInt")),
        to_str(props.get("Name")),
        to_float(props.get("MiscloseRatio")),
        to_float(props.get("MiscloseDistance")),
        to_int(props.get("County")),
        to_str(props.get("created_user")),
        to_ts_ms(props.get("created_date")),
        to_str(props.get("last_edited_user")),
        to_ts_ms(props.get("last_edited_date")),
        to_ts_ms(props.get("AutoDate")),
        to_str(props.get("AutoMethod")),
        to_str(props.get("AutoWho")),
        to_str(props.get("GlobalID")),
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
