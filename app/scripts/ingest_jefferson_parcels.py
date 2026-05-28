"""
Ingest Jefferson County taxlot parcels into parcels_jefferson.

Source: maps.deschutes.org/arcgis/rest/services/911/COFSA_Map/MapServer/3
        ("Jefferson County Taxlots", Map,Query,Data, maxRecordCount=1000, 12,813 lots).

Why THIS source — the load-bearing decision (read before "fixing" this):
  Jefferson has no county-direct queryable FeatureServer reachable from the
  droplet (gis.jeffersoncountyor.gov is Cloudflare-403). The obvious statewide
  fallback, the ODF TaxlotsDisplay service (gis.odf.oregon.gov .../MapServer/15),
  carries the full Yamhill-family attribute schema in its metadata — BUT its
  service capabilities are "Map" only. It has NO Query capability: every /query
  call returns {"code":400,"message":"Requested operation is not supported"}.
  Display-reachable is not queryable. A prior cut of this file pointed at ODF/15
  and would have died on the first fetch_page — verified 2026-05-28.

  The Deschutes-hosted COFSA fire-mapping service (same host we already use for
  Deschutes County) aggregates neighboring counties and hosts a REAL queryable
  "Jefferson County Taxlots" layer with owner + situs + acres + geometry.
  Teaching note for Nevsky: when a county's own endpoint is missing or display-
  only, the data often lives inside a *regional* aggregator — fire districts,
  911 dispatch, COGs. Grep the neighbor's server before declaring it unreachable.
  And always check `capabilities` for "Query" — a layer can advertise a gorgeous
  schema you can see one record at a time (Identify) yet refuse to bulk-export.

Schema decision (Iosif, 2026-05-28): keep the rich ~50-col Yamhill-family table
  (migrations/2026-05-28_parcels_jefferson.sql) even though COFSA only fills ~14
  of its columns. The unmapped columns (PLSS, mailing block, owner_line2/3,
  instrument snapshot, prp_class) stay NULL-but-present per KB_87 forward-compat,
  so an upstream backfill (county-direct or DOR_ORMAP, once reachable) surfaces
  transparently instead of needing a migration. COFSA fields that have no rich
  column (SUBDIVISIO, LOT) are preserved verbatim in raw_properties — nothing is
  dropped. Honest IN FLIGHT per DOCTRINE-HONEST-IN-FLIGHT-01: no valuation, no
  building details, no PLSS, no mailing address in this source.

COFSA field set (layer 3):
  OBJECTID, MAPTAXLOT, OWNER, HOUSE_NUMB, PRE_DIRECT, STREET_NAM, STREET_TYP,
  DIRECTION, UNIT_NUMBE, CITY, ZIP, SUBDIVISIO, LOT, ACRES, Shape

Strategy: paginated geojson query (server supports f=geojson; reprojects its
  native EPSG:3857 to 4326 on outSR=4326). Idempotent: ON CONFLICT (objectid)
  DO UPDATE. Page size == server maxRecordCount (1000); orderByFields=OBJECTID
  so pagination can't skip/dupe.

Run inside the API container (psycopg3 + urllib only — no requests/psycopg2 dep):
  docker exec nevsky-api python -m scripts.ingest_jefferson_parcels
"""
import os
import json
import time
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

PARCEL_URL = (
    "https://maps.deschutes.org/arcgis/rest/services/"
    "911/COFSA_Map/MapServer/3/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 1000   # == server maxRecordCount; exceeding it gets silently capped
BATCH_SIZE = 500
HTTP_TIMEOUT = 90
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


PARCEL_INSERT_SQL = """
INSERT INTO parcels_jefferson (
    objectid,
    taxlot, maptaxlot, ortaxlot, map_taxlot_short, map_number, ormapnum, special_int,
    plss_county, plss_town, plss_town_part, plss_town_dir,
    plss_range, plss_range_part, plss_range_dir,
    plss_section, plss_qtr, plss_qtr_qtr, plss_anomaly,
    map_suf_type, map_suf_num, map_class, map_rel_code, relia_code,
    prim_acc_num, si_map_tax, ref_link,
    owner_line1, owner_line2, owner_line3, agent_name,
    mail_add1, mail_add2, mail_city, mail_state, mail_zip, mail_country,
    site_add_nam, site_add_cty, site_zip,
    inst_year, inst_month, inst_id, inst_type,
    dwelling, prp_class, prp_cls_desc,
    taxlot_acres, taxlot_feet, shape_area, shape_length,
    geom, raw_properties
) VALUES (
    %s,
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s,
    -- COFSA's Esri->geojson rings carry winding-order self-intersections on a
    -- handful of lots (14 of 12,813 on first ingest). ST_MakeValid fixes them;
    -- ST_CollectionExtract(...,3) keeps only polygonal parts (MakeValid can emit
    -- a GeometryCollection) so the GEOMETRY(MultiPolygon,4326) column never rejects.
    ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    maptaxlot = EXCLUDED.maptaxlot,
    owner_line1 = EXCLUDED.owner_line1,
    site_add_nam = EXCLUDED.site_add_nam,
    site_add_cty = EXCLUDED.site_add_cty,
    site_zip = EXCLUDED.site_zip,
    taxlot_acres = EXCLUDED.taxlot_acres,
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


def compose_situs(props):
    """Build one readable situs street line from COFSA's six components.
    COFSA ships blanks as single-space strings (' '); to_str strips those to
    None so we don't emit '  NE  ST' garbage. Returns None when no real parts."""
    parts = [
        to_str(props.get("HOUSE_NUMB")),
        to_str(props.get("PRE_DIRECT")),
        to_str(props.get("STREET_NAM")),
        to_str(props.get("STREET_TYP")),
        to_str(props.get("DIRECTION")),
    ]
    line = " ".join(p for p in parts if p)
    unit = to_str(props.get("UNIT_NUMBE"))
    if unit:
        line = f"{line} #{unit}".strip()
    return line or None


def parcel_row(feature):
    props = feature.get("properties", {}) or {}
    geom = feature.get("geometry")
    if geom is None:
        return None
    objectid = to_int(props.get("OBJECTID"))
    # MAPTAXLOT is the only natural key COFSA gives us; taxlot is NOT NULL.
    maptaxlot = to_str(props.get("MAPTAXLOT"))
    if objectid is None or maptaxlot is None:
        return None
    return (
        objectid,
        # identifiers — COFSA only carries MAPTAXLOT; mirror it into taxlot.
        maptaxlot,                              # taxlot (NOT NULL)
        maptaxlot,                              # maptaxlot
        None,                                   # ortaxlot        — IN FLIGHT
        None,                                   # map_taxlot_short — IN FLIGHT
        None,                                   # map_number      — IN FLIGHT
        None,                                   # ormapnum        — IN FLIGHT
        None,                                   # special_int     — IN FLIGHT
        # PLSS — not in COFSA
        None, None, None, None,
        None, None, None,
        None, None, None, None,
        # Map suffix / class — not in COFSA
        None, None, None, None, None,
        # Account / link — not in COFSA
        None, None, None,
        # Ownership — COFSA single OWNER string -> owner_line1
        to_str(props.get("OWNER")),             # owner_line1
        None,                                   # owner_line2     — IN FLIGHT
        None,                                   # owner_line3     — IN FLIGHT
        None,                                   # agent_name      — IN FLIGHT
        # Mailing — COFSA address fields are SITUS, not mailing
        None, None, None, None, None, None,
        # Situs — composed from the six COFSA components
        compose_situs(props),                   # site_add_nam
        to_str(props.get("CITY")),              # site_add_cty
        to_str(props.get("ZIP")),               # site_zip
        # Recording snapshot — not in COFSA
        None, None, None, None,
        # Classification — not in COFSA
        None, None, None,
        # Acreage / area
        to_float(props.get("ACRES")),           # taxlot_acres
        None,                                   # taxlot_feet     — IN FLIGHT
        None,                                   # shape_area      — IN FLIGHT
        None,                                   # shape_length    — IN FLIGHT
        # Geom + raw (raw preserves SUBDIVISIO / LOT and everything else verbatim)
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
        print(f"[parcels_jefferson] page {page_num} offset={offset} got {len(feats)} | total: {total} | skipped: {skipped} | elapsed: {elapsed:.1f}s")

        if len(feats) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    elapsed = time.time() - t0
    print(f"[parcels_jefferson] DONE: {total} ingested, {skipped} skipped, {elapsed:.1f}s")
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
