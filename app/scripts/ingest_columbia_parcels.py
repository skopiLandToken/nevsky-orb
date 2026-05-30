"""
Ingest Columbia County taxlot parcels into parcels_columbia (TERRA — county #19).

Source: gis.columbiacountymaps.com/server/rest/services/TaxlotWb/FeatureServer/0
        ("Taxlots", capabilities=Query,Extract, maxRecordCount=2000, 28,806 lots).

Why THIS source — the load-bearing decision (read before "fixing" this):
  Columbia's county-direct GIS host (gis.columbiacountyor.gov) is NXDOMAIN from the
  droplet, and columbiacountymaps.com is a Squarespace placeholder. The public web-
  map APP (ColumbiaCountyWebMaps) went offline 2026-04-22 for an ADA/WCAG upgrade —
  but the underlying ArcGIS Server REST service stayed live and queryable. Teaching
  note for Nevsky: a county pulling its human-facing web-map app offline does NOT
  imply the REST server is down — probe /server/rest/services directly. And per the
  Jefferson lesson: confirm `capabilities` includes "Query" (TaxlotWb/0 is
  Query,Extract — bulk-exportable, unlike the ODF TaxlotsDisplay Map-only trap).

OID quirk: the source's true OID field is OBJECTID_1; the published OBJECTID integer
  column is 100% NULL. We key objectid + pagination off OBJECTID_1. Ordering by it
  keeps pagination from skipping/duping (server maxRecordCount=2000).

Columbia is POLK/MARION-RICH: TaxlotWb/0 joins the assessor account to the polygon
  in one row — OWNER, AGENT, mailing block, PRIMARY_SI (combined situs), ACCOUNT_ID,
  PROPERTY_C, NUMHOUSES/NUMBUILDIN, ACRES/SQFT, and ACCELA_MT (the Accela permit key).

Situs parse: PRIMARY_SI is one combined "{street} {CITY} OR {zip}" string. We keep it
  verbatim in primary_situs (always display-correct) and best-effort split street/
  city/zip via a KNOWN-CITY suffix match (the only reliable split when street and
  city share one space-delimited field). No known-city match -> street/city stay
  NULL, primary_situs carries the full line. No fabricated splits.

IN FLIGHT (DOCTRINE-HONEST-IN-FLIGHT-01): no valuation (assessed or RMV) in the REST
  layer; no building details beyond dwelling/building counts; deeds/sale chain live
  on the separate `Sales` FeatureServer (queued as a records L3 follow-up); zoning
  not yet located in the Land_Development folder.

Run inside the API container (psycopg3 + urllib only — no requests/psycopg2 dep):
  docker exec nevsky-api python -m scripts.ingest_columbia_parcels
"""
import os
import re
import json
import time
from urllib.request import Request, urlopen

import psycopg


DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

PARCEL_URL = (
    "https://gis.columbiacountymaps.com/server/rest/services/"
    "TaxlotWb/FeatureServer/0/query"
)
USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"
PAGE_SIZE = 2000   # == server maxRecordCount; exceeding it gets silently capped
BATCH_SIZE = 500
HTTP_TIMEOUT = 90
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 5

# Columbia County incorporated cities + CDPs / named places, used as the situs
# suffix dictionary. Longest-first so "COLUMBIA CITY" / "DEER ISLAND" / "ST HELENS"
# match before any shorter substring. PORTLAND included for the north-Sauvie-Island
# slice of Columbia that carries Portland-addressed situs.
KNOWN_CITIES = [
    "COLUMBIA CITY", "DEER ISLAND", "SAINT HELENS", "ST HELENS",
    "CLATSKANIE", "SCAPPOOSE", "BIRKENFELD", "MARSHLAND",
    "VERNONIA", "PITTSBURG", "TREHARNE", "RAINIER", "PRESCOTT",
    "PORTLAND", "APIARY", "ALSTON", "QUINCY", "YANKTON", "WARREN",
    "GOBLE", "NATAL", "MIST",
]
_KNOWN_CITIES_SORTED = sorted(set(KNOWN_CITIES), key=len, reverse=True)


PARCEL_INSERT_SQL = """
INSERT INTO parcels_columbia (
    objectid,
    taxlot, maptaxlot, map_number, accela_mt, account_id,
    owner_line1, agent_name, mail_add1, mail_city, mail_state, mail_zip,
    primary_situs, site_add_nam, site_add_cty, site_zip,
    prp_class, num_houses, num_buildings, dwelling,
    taxlot_acres, taxlot_sqft, size_label, shape_area, shape_length,
    geom, raw_properties
) VALUES (
    %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    -- Esri->geojson rings can carry winding-order self-intersections on a handful
    -- of lots. ST_MakeValid fixes them; ST_CollectionExtract(...,3) keeps only
    -- polygonal parts (MakeValid can emit a GeometryCollection) so the
    -- GEOMETRY(MultiPolygon,4326) column never rejects.
    ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    maptaxlot = EXCLUDED.maptaxlot,
    map_number = EXCLUDED.map_number,
    accela_mt = EXCLUDED.accela_mt,
    account_id = EXCLUDED.account_id,
    owner_line1 = EXCLUDED.owner_line1,
    agent_name = EXCLUDED.agent_name,
    mail_add1 = EXCLUDED.mail_add1,
    mail_city = EXCLUDED.mail_city,
    mail_state = EXCLUDED.mail_state,
    mail_zip = EXCLUDED.mail_zip,
    primary_situs = EXCLUDED.primary_situs,
    site_add_nam = EXCLUDED.site_add_nam,
    site_add_cty = EXCLUDED.site_add_cty,
    site_zip = EXCLUDED.site_zip,
    prp_class = EXCLUDED.prp_class,
    num_houses = EXCLUDED.num_houses,
    num_buildings = EXCLUDED.num_buildings,
    dwelling = EXCLUDED.dwelling,
    taxlot_acres = EXCLUDED.taxlot_acres,
    taxlot_sqft = EXCLUDED.taxlot_sqft,
    size_label = EXCLUDED.size_label,
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
        f"&orderByFields=OBJECTID_1"
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


def parse_situs(primary_si):
    """Split PRIMARY_SI ('{street} {CITY} OR {zip}') into (street, city, zip).

    zip = trailing 5-digit (optional +4). city = longest known-Columbia-place
    suffix match on the remaining "{street} {CITY}" core after stripping the
    trailing state token 'OR'. Returns (None, None, zip) when no known city
    matches — primary_situs keeps the verbatim line, so nothing is lost and no
    wrong street/city split is invented.
    """
    s = to_str(primary_si)
    if not s:
        return (None, None, None)
    su = s.upper()

    zipc = None
    mz = re.search(r"(\d{5})(?:-\d{4})?\s*$", su)
    core = su
    if mz:
        zipc = mz.group(1)
        core = su[: mz.start()].strip()
    # drop a trailing state token (OR), with or without a comma
    core = re.sub(r"[, ]+OR\.?$", "", core).strip()

    for c in _KNOWN_CITIES_SORTED:
        if core == c:
            return (None, c, zipc)
        if core.endswith(" " + c):
            street = core[: -(len(c) + 1)].strip() or None
            return (street, c, zipc)
    return (None, None, zipc)


def parcel_row(feature):
    props = feature.get("properties", {}) or {}
    geom = feature.get("geometry")
    if geom is None:
        return None
    objectid = to_int(props.get("OBJECTID_1"))
    # MAP_TAX is the natural taxlot key; taxlot is NOT NULL.
    map_tax = to_str(props.get("MAP_TAX"))
    if objectid is None or map_tax is None:
        return None

    num_houses = to_int(props.get("NUMHOUSES"))
    primary_si = to_str(props.get("PRIMARY_SI"))
    street, city, zipc = parse_situs(primary_si)

    return (
        objectid,
        # identifiers
        map_tax,                                 # taxlot (NOT NULL)
        map_tax,                                 # maptaxlot (mirror)
        to_str(props.get("MAPNUM")),             # map_number
        to_str(props.get("ACCELA_MT")),          # accela_mt (Accela permit key)
        to_int(props.get("ACCOUNT_ID")),         # account_id
        # ownership / mailing
        to_str(props.get("OWNER")),              # owner_line1
        to_str(props.get("AGENT")),              # agent_name (source ships ' ' blanks -> None)
        to_str(props.get("M_ADDRESS")),          # mail_add1
        to_str(props.get("M_CITY")),             # mail_city
        to_str(props.get("M_STATE")),            # mail_state
        to_str(props.get("ZIP")),                # mail_zip
        # situs
        primary_si,                              # primary_situs (verbatim)
        street,                                  # site_add_nam (parsed; may be None)
        city,                                    # site_add_cty (known-city match; may be None)
        zipc,                                    # site_zip (parsed)
        # classification / structures
        to_str(props.get("PROPERTY_C")),         # prp_class
        num_houses,                              # num_houses
        to_int(props.get("NUMBUILDIN")),         # num_buildings
        ("Y" if (num_houses or 0) > 0 else "N"), # dwelling
        # acreage / area
        to_float(props.get("ACRES")),            # taxlot_acres
        to_int(props.get("SQFT")),               # taxlot_sqft
        to_str(props.get("SIZE")),               # size_label
        to_float(props.get("Shape__Area")),      # shape_area
        to_float(props.get("Shape__Length")),    # shape_length
        # geom + raw (raw preserves IMAGE/IMAGE2/MA/SA/CODE/DESCRIPTIO/UNIT_ID/etc.)
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
        print(f"[parcels_columbia] page {page_num} offset={offset} got {len(feats)} | total: {total} | skipped: {skipped} | elapsed: {elapsed:.1f}s")

        if len(feats) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    elapsed = time.time() - t0
    print(f"[parcels_columbia] DONE: {total} ingested, {skipped} skipped, {elapsed:.1f}s")
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
