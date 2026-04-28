"""
Ingest Deschutes County taxlot parcels into parcels_deschutes table.

Source: /tmp/deschutes_taxlots.json (176MB GeoJSON FeatureCollection)
Strategy: streaming parse with ijson, batch insert 500 features at a time.

Idempotent: ON CONFLICT (objectid) DO UPDATE — re-runs refresh existing rows
with newer geometry/properties without duplicating.

Memory: holds at most ~500 features in RAM at any moment (~5-10MB peak).
"""
import ijson
import json
import psycopg
from decimal import Decimal


class DecimalEncoder(json.JSONEncoder):
    """ijson returns numbers as Decimal for precision; convert to float for JSON."""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)

from decimal import Decimal


class DecimalEncoder(json.JSONEncoder):
    """ijson returns numbers as Decimal for precision; convert to float for JSON."""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)

import os
import sys
import time
from pathlib import Path

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")
SOURCE_FILE = "/tmp/deschutes_taxlots.json"
BATCH_SIZE = 500

INSERT_SQL = """
INSERT INTO parcels_deschutes (
    objectid, taxlot, township, range_, section, quarter, sixteenth,
    parcel_num, mapsup, mapnumber, shape_length, shape_area, dial_url,
    geom, raw_properties
) VALUES (
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
    %s::jsonb
)
ON CONFLICT (objectid) DO UPDATE SET
    taxlot = EXCLUDED.taxlot,
    township = EXCLUDED.township,
    range_ = EXCLUDED.range_,
    section = EXCLUDED.section,
    quarter = EXCLUDED.quarter,
    sixteenth = EXCLUDED.sixteenth,
    parcel_num = EXCLUDED.parcel_num,
    mapsup = EXCLUDED.mapsup,
    mapnumber = EXCLUDED.mapnumber,
    shape_length = EXCLUDED.shape_length,
    shape_area = EXCLUDED.shape_area,
    dial_url = EXCLUDED.dial_url,
    geom = EXCLUDED.geom,
    raw_properties = EXCLUDED.raw_properties,
    ingested_at = NOW();
"""


def feature_to_row(feature):
    """Extract row tuple from a GeoJSON feature."""
    props = feature.get("properties", {}) or {}
    geom = feature.get("geometry")
    if geom is None:
        return None
    return (
        props.get("OBJECTID"),
        props.get("TAXLOT") or "UNKNOWN",
        props.get("TOWNSHIP"),
        props.get("RANGE"),
        props.get("SECTION"),
        props.get("QUARTER"),
        props.get("SIXTEENTH"),
        props.get("PARCEL"),
        props.get("MAPSUP"),
        props.get("MAPNUMBER"),
        props.get("Shape_Length"),
        props.get("Shape_Area"),
        props.get("DIAL"),
        json.dumps(geom, cls=DecimalEncoder),
        json.dumps(props, cls=DecimalEncoder),
    )


def main():
    src = Path(SOURCE_FILE)
    if not src.exists():
        print(f"ERROR: Source file not found: {SOURCE_FILE}")
        sys.exit(1)

    size_mb = src.stat().st_size / (1024 * 1024)
    print(f"Source: {SOURCE_FILE} ({size_mb:.1f} MB)")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"DSN: {DB_DSN.replace(DB_DSN.split('@')[0].split(':')[-1], '***')}")
    print("Starting ingest...\n")

    start = time.time()
    inserted = 0
    skipped = 0
    failed = 0
    batch = []

    with open(SOURCE_FILE, "rb") as f, psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            for feature in ijson.items(f, "features.item"):
                row = feature_to_row(feature)
                if row is None:
                    skipped += 1
                    continue
                batch.append(row)

                if len(batch) >= BATCH_SIZE:
                    try:
                        cur.executemany(INSERT_SQL, batch)
                        conn.commit()
                        inserted += len(batch)
                    except Exception as e:
                        conn.rollback()
                        failed += len(batch)
                        print(f"  ✗ Batch failed at row {inserted}: {e}")
                    batch = []

                    elapsed = time.time() - start
                    rate = inserted / elapsed if elapsed > 0 else 0
                    print(f"  ✓ {inserted:,} ingested ({rate:.0f}/sec, {elapsed:.1f}s elapsed)")

            # Final batch
            if batch:
                try:
                    cur.executemany(INSERT_SQL, batch)
                    conn.commit()
                    inserted += len(batch)
                except Exception as e:
                    conn.rollback()
                    failed += len(batch)
                    print(f"  ✗ Final batch failed: {e}")

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"Inserted/updated: {inserted:,}")
    print(f"Skipped (no geom): {skipped:,}")
    print(f"Failed:           {failed:,}")
    print(f"Elapsed:          {elapsed:.1f}s ({inserted/elapsed:.0f} features/sec)" if elapsed > 0 else "")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
