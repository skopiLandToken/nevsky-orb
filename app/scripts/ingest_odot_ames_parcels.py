"""
Ingest a county's taxlots from ODOT's statewide "ames" cadastral into parcels_<county>.

Source: gis.odot.state.or.us/arcgis1006/rest/services/ames/ames/MapServer/<LAYER>
        (per-county "X County Taxlots" layers, capabilities=Map,Query,Data,
         maxRecordCount 5000, geojson + outSR=4326 reprojection).

Why THIS source — load-bearing (read before "fixing"):
  ODOT's ames MapServer is the queryable statewide cadastral fallback for counties
  with no county-direct REST and only a display-only ODF layer. It is FULLY
  queryable (unlike ODF TaxlotsDisplay, which is Map-only) and is the ONLY reachable
  queryable taxlot source for Baker / Curry / Grant / Harney / Klamath / Lake /
  Wheeler. See migrations/2026-05-30_parcels_baker.sql for the full reasoning.

THIN by nature: ODOT keeps this cadastral for road / right-of-way mapping, so it
  ships ONLY geometry + taxlot identity (County, MapNumber, Taxlot, MapTaxlot,
  EFFECTV_DT). No owner / situs / acreage / valuation — that assessor data is not in
  any free public layer for counties this small. owner/situs columns stay NULL;
  acreage is computed from the polygon at read time (ST_Area), not stored. This one
  script is the reusable ingest for every ODOT-ames county — pass --layer/--table/
  --fips.

Reusable across the ODOT-ames cohort:
  python -m scripts.ingest_odot_ames_parcels --layer 28 --table parcels_baker  --fips 001  # Baker (default)
  python -m scripts.ingest_odot_ames_parcels --layer 31 --table parcels_curry  --fips 015  # Curry
  python -m scripts.ingest_odot_ames_parcels --layer 32 --table parcels_grant  --fips 023  # Grant
  python -m scripts.ingest_odot_ames_parcels --layer 33 --table parcels_harney --fips 025  # Harney
  python -m scripts.ingest_odot_ames_parcels --layer 36 --table parcels_klamath --fips 035 # Klamath
  python -m scripts.ingest_odot_ames_parcels --layer 37 --table parcels_lake   --fips 037  # Lake
  python -m scripts.ingest_odot_ames_parcels --layer 40 --table parcels_wheeler --fips 069 # Wheeler

Run inside the API container (psycopg3 + urllib only — no requests/psycopg2 dep).
"""
import os
import sys
import json
import time
import argparse
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

AMES_BASE = "https://gis.odot.state.or.us/arcgis1006/rest/services/ames/ames/MapServer"
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 5000   # == server maxRecordCount
BATCH_SIZE = 500
HTTP_TIMEOUT = 120
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5


def insert_sql(table: str) -> str:
    # Table name is from a fixed CLI allowlist (see main); never user-web input.
    return f"""
INSERT INTO {table} (
    objectid,
    taxlot, maptaxlot, map_taxlot_short, map_number, effective_year,
    owner_line1, site_add_nam, site_add_cty,
    geom, raw_properties
) VALUES (
    %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s,
    -- Esri->geojson rings can carry winding-order self-intersections on a handful of
    -- lots. ST_MakeValid fixes them; ST_CollectionExtract(...,3) keeps only polygonal
    -- parts so the GEOMETRY(MultiPolygon,4326) column never rejects.
    ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    maptaxlot = EXCLUDED.maptaxlot,
    map_taxlot_short = EXCLUDED.map_taxlot_short,
    map_number = EXCLUDED.map_number,
    effective_year = EXCLUDED.effective_year,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    ingested_at = NOW();
"""


def fetch_page(layer: int, offset: int) -> dict:
    url = (
        f"{AMES_BASE}/{layer}/query?where=1%3D1"
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
    raise RuntimeError(f"fetch_page layer={layer} offset={offset} failed after {HTTP_RETRIES}: {last_err}")


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
    maptaxlot = to_str(props.get("MapTaxlot"))
    if objectid is None or maptaxlot is None:
        return None
    return (
        objectid,
        maptaxlot,                               # taxlot (NOT NULL)
        maptaxlot,                               # maptaxlot
        to_str(props.get("Taxlot")),             # map_taxlot_short
        to_str(props.get("MapNumber")),          # map_number
        to_str(props.get("EFFECTV_DT")),         # effective_year
        None,                                    # owner_line1  — not in ODOT cadastral
        None,                                    # site_add_nam — not in ODOT cadastral
        None,                                    # site_add_cty — not in ODOT cadastral
        json.dumps(geom),
        json.dumps(props),
    )


def _iter_pages(layer: int, infile: str):
    """Yield FeatureCollection-ish dicts. Live mode pages the ODOT HTTP query.
    File mode reads ONE combined geojson FeatureCollection from `infile` — used when
    the container can't egress to ODOT (docker-bridge block); the host fetches the
    pages and writes the combined file to a mounted path, container loads from it."""
    if infile:
        with open(infile) as fh:
            doc = json.load(fh)
        yield doc
        return
    offset = 0
    while True:
        page = fetch_page(layer, offset)
        feats = page.get("features", []) or []
        yield page
        more = page.get("exceededTransferLimit") or page.get("properties", {}).get("exceededTransferLimit")
        if len(feats) < PAGE_SIZE and not more:
            break
        offset += PAGE_SIZE


def ingest(conn, layer: int, table: str, infile: str = None) -> int:
    sql = insert_sql(table)
    t0 = time.time()
    total = 0
    skipped = 0
    page_num = 0
    for page in _iter_pages(layer, infile):
        page_num += 1
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
                    cur.executemany(sql, batch)
                conn.commit()
                total += len(batch)
                batch = []

        if batch:
            with conn.cursor() as cur:
                cur.executemany(sql, batch)
            conn.commit()
            total += len(batch)

        elapsed = time.time() - t0
        print(f"[{table}] page {page_num} got {len(feats)} | total: {total} | skipped: {skipped} | elapsed: {elapsed:.1f}s")

    elapsed = time.time() - t0
    print(f"[{table}] DONE: {total} ingested, {skipped} skipped, {elapsed:.1f}s")
    return total


# CLI allowlist — table names are fixed here, never taken from web input.
KNOWN = {
    "parcels_baker": (28, "001"), "parcels_curry": (31, "015"),
    "parcels_grant": (32, "023"), "parcels_harney": (33, "025"),
    "parcels_klamath": (36, "035"), "parcels_lake": (37, "037"),
    "parcels_wheeler": (40, "069"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", type=int, default=28)
    ap.add_argument("--table", default="parcels_baker")
    ap.add_argument("--fips", default="001")
    ap.add_argument("--infile", default=None,
                    help="Load features from a local combined-geojson file instead of "
                         "HTTP (host fetches when the container can't egress to ODOT).")
    args = ap.parse_args()
    if args.table not in KNOWN:
        print(f"Refusing unknown table {args.table!r}; add it to KNOWN first.", file=sys.stderr)
        sys.exit(2)

    conn = psycopg.connect(DB_DSN)
    conn.autocommit = False
    try:
        n = ingest(conn, args.layer, args.table, args.infile)
        print(f"\nALL DONE: {args.table} parcels={n}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
