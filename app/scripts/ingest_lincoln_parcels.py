"""
Ingest Lincoln County taxlot parcels into parcels_lincoln (THIN / Washington-pattern).

Source: https://services3.arcgis.com/MiIvpOH5WqZfaAvr/arcgis/rest/services/
        taxlot21/FeatureServer/0
        (Hosted ArcGIS Online ORMAP cadastre layer. Lincoln County runs no ArcGIS
        Server — its public map is GeoMOOSE/UMN-MapServer/WFS, not ArcGIS-queryable —
        and the ODF MapServer has query disabled, so this hosted cadastre layer is
        the canonical reachable L1 source. 46,852 parcels; maxRecordCount 2000;
        source PK FID.)

This layer ships ONLY ORMAP polygon attributes — taxlot identifiers, PLSS, map
suffix/class, acreage, REFLink. NO owner / situs / account / valuation / instrument
(those columns exist in parcels_lincoln but stay NULL on first ingest; the queued
Newport-UGB enrichment backfills them by ORTaxlot — see the migration header).

Run inside the API container:
  docker compose exec -T api python -m scripts.ingest_lincoln_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

PARCEL_URL = (
    "https://services3.arcgis.com/MiIvpOH5WqZfaAvr/arcgis/rest/services/"
    "taxlot21/FeatureServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 2000   # server maxRecordCount for this FeatureServer layer
BATCH_SIZE = 500
HTTP_TIMEOUT = 90
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


PARCEL_INSERT_SQL = """
INSERT INTO parcels_lincoln (
    fid,
    taxlot, maptaxlot, ortaxlot, map_taxlot_short, map_number, ormapnum, special_int,
    plss_county, plss_town, plss_town_part, plss_town_dir,
    plss_range, plss_range_part, plss_range_dir,
    plss_section, plss_qtr, plss_qtr_qtr, plss_anomaly,
    map_suf_type, map_suf_num, map_class, map_rel_code, relia_code, ref_link,
    taxlot_acres, taxlot_feet, shape_area, shape_length,
    geom, raw_properties
) VALUES (
    %s,
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3)),
    %s::jsonb
)
ON CONFLICT (fid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    maptaxlot = EXCLUDED.maptaxlot,
    ortaxlot = EXCLUDED.ortaxlot,
    map_taxlot_short = EXCLUDED.map_taxlot_short,
    map_number = EXCLUDED.map_number,
    ormapnum = EXCLUDED.ormapnum,
    special_int = EXCLUDED.special_int,
    plss_county = EXCLUDED.plss_county,
    plss_town = EXCLUDED.plss_town,
    plss_town_part = EXCLUDED.plss_town_part,
    plss_town_dir = EXCLUDED.plss_town_dir,
    plss_range = EXCLUDED.plss_range,
    plss_range_part = EXCLUDED.plss_range_part,
    plss_range_dir = EXCLUDED.plss_range_dir,
    plss_section = EXCLUDED.plss_section,
    plss_qtr = EXCLUDED.plss_qtr,
    plss_qtr_qtr = EXCLUDED.plss_qtr_qtr,
    plss_anomaly = EXCLUDED.plss_anomaly,
    map_suf_type = EXCLUDED.map_suf_type,
    map_suf_num = EXCLUDED.map_suf_num,
    map_class = EXCLUDED.map_class,
    map_rel_code = EXCLUDED.map_rel_code,
    relia_code = EXCLUDED.relia_code,
    ref_link = EXCLUDED.ref_link,
    taxlot_acres = EXCLUDED.taxlot_acres,
    taxlot_feet = EXCLUDED.taxlot_feet,
    shape_area = EXCLUDED.shape_area,
    shape_length = EXCLUDED.shape_length,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    ingested_at = NOW();
"""


def fetch_page(offset: int) -> dict:
    url = (
        f"{PARCEL_URL}?where=1%3D1"
        f"&outFields=*&outSR=4326&f=geojson"
        f"&resultOffset={offset}&resultRecordCount={PAGE_SIZE}"
        f"&orderByFields=FID"
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
    fid = to_int(props.get("FID"))
    taxlot = to_str(props.get("ORTaxlot"))
    if fid is None or taxlot is None:
        return None
    return (
        fid,
        # identifiers
        taxlot,
        to_str(props.get("MapTaxlot")),
        to_str(props.get("ORTaxlot")),
        to_str(props.get("Taxlot")),
        to_str(props.get("MapNumber")),
        to_str(props.get("ORMapNum")),
        to_str(props.get("SpecialInt")),
        # PLSS
        to_int(props.get("County")),
        to_int(props.get("Town")),
        to_float(props.get("TownPart")),
        to_str(props.get("TownDir")),
        to_int(props.get("Range")),
        to_float(props.get("RangePart")),
        to_str(props.get("RangeDir")),
        to_int(props.get("SecNumber")),
        to_str(props.get("Qtr")),
        to_str(props.get("QtrQtr")),
        to_str(props.get("Anomaly")),
        # Map suffix / class
        to_str(props.get("MapSufType")),
        to_int(props.get("MapSufNum")),
        to_str(props.get("MapClass")),
        to_str(props.get("MapRelCode")),
        to_int(props.get("ReliaCode")),
        to_str(props.get("REFLink")),
        # Acreage / area
        to_float(props.get("TaxlotAcre")),
        to_int(props.get("TaxLotFeet")),
        to_float(props.get("Shape__Area")),
        to_float(props.get("Shape__Length")),
        # Geom + raw
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
        print(f"[parcels_lincoln] page {page_num} offset={offset} got {len(feats)} | total: {total} | skipped: {skipped} | elapsed: {elapsed:.1f}s")

        if len(feats) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    elapsed = time.time() - t0
    print(f"[parcels_lincoln] DONE: {total} ingested, {skipped} skipped, {elapsed:.1f}s")
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
