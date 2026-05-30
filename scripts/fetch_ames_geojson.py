"""
Host-side fetch helper for ODOT's "ames" statewide taxlot cadastral.

WHY THIS RUNS ON THE HOST, NOT IN THE CONTAINER (load-bearing — read before moving it):
  The nevsky-api container CANNOT egress to gis.odot.state.or.us (167.131.109.106) —
  curl/urllib from inside the container time out (http=000), while the SAME request
  from the droplet host succeeds. It's a docker-bridge-vs-host reachability asymmetry
  to that specific State-of-Oregon host. So we fetch on the host with this helper,
  write a combined geojson to a path mounted into the container (app/_ames_tmp/, which
  the container sees at /app/_ames_tmp/), and load it container-side via
  scripts.ingest_odot_ames_parcels --infile.

WHY PER-PAGE RESUME: ODOT's ames service intermittently returns a ~7.7KB HTML error
  page instead of geojson for large requests (the full geojson page is multi-MB). A
  single bad page must not lose the whole run. This fetcher writes each page to its
  own file under a work dir, retries each page hard, SKIPS pages already on disk
  (resumable — just re-run), and only combines once every page is present. Page size
  is kept small (1000) to shrink each response and reduce the HTML-error rate.

Usage (on the droplet host, NOT in the container):
  python3 scripts/fetch_ames_geojson.py --layer 28 --out app/_ames_tmp/baker.geojson
"""
import os
import sys
import json
import time
import argparse
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "SKOpi-TERRA/1.0 (+https://skopi.io)"
AMES = "https://gis.odot.state.or.us/arcgis1006/rest/services/ames/ames/MapServer"
PAGE = 1000
MAX_ATTEMPTS = 20
_print_lock = threading.Lock()


def _log(msg):
    with _print_lock:
        print(msg, flush=True)


def get_count(layer):
    url = f"{AMES}/{layer}/query?where=1%3D1&returnCountOnly=true&f=json"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            d = json.loads(urllib.request.urlopen(req, timeout=60).read())
            if "count" in d:
                return int(d["count"])
        except Exception:
            time.sleep(min(2 * attempt, 15))
    raise RuntimeError(f"count for layer {layer} failed")


def get_page(layer, off):
    url = (f"{AMES}/{layer}/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
           f"&resultOffset={off}&resultRecordCount={PAGE}&orderByFields=OBJECTID")
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            raw = urllib.request.urlopen(req, timeout=90).read()
            d = json.loads(raw)            # raises if ODOT returned its HTML error page
            if "features" not in d:
                raise ValueError("no features key")
            return d
        except Exception as e:
            last = e
            time.sleep(min(3 * attempt, 20))
    raise RuntimeError(f"layer {layer} offset {off} failed after {MAX_ATTEMPTS} attempts: {last}")


def fetch_one(layer, off, workdir):
    """Fetch a single offset page to its per-page file (skip if cached). Returns
    (off, n_features). Raises on hard failure so the caller can report which page."""
    pf = os.path.join(workdir, f"p{off:07d}.json")
    if os.path.exists(pf) and os.path.getsize(pf) > 0:
        with open(pf) as fh:
            n = len(json.load(fh).get("features", []) or [])
        _log(f"  off={off} cached ({n})")
        return off, n
    d = get_page(layer, off)
    tmp = pf + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh)
    os.replace(tmp, pf)  # atomic — a partial write never looks complete to a resume
    n = len(d.get("features", []) or [])
    _log(f"  off={off} fetched ({n})")
    return off, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", type=int, required=True)
    ap.add_argument("--out", required=True, help="combined geojson output path")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent fetch workers — parallelism bypasses ODOT's "
                         "per-page flaky-retry grind (a stuck page can't block the rest).")
    args = ap.parse_args()

    workdir = args.out + ".pages"
    os.makedirs(workdir, exist_ok=True)

    count = get_count(args.layer)
    offsets = list(range(0, count, PAGE))
    _log(f"layer {args.layer}: {count} features -> {len(offsets)} pages, {args.workers} workers")

    # Fan out all offsets across the worker pool. Each retries internally; a page
    # that exhausts retries surfaces here so we know exactly which offset to chase,
    # instead of one stuck page stalling the whole serial run.
    failed = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, args.layer, off, workdir): off for off in offsets}
        for fut in as_completed(futs):
            off = futs[fut]
            try:
                fut.result()
            except Exception as e:
                _log(f"  off={off} FAILED: {e}")
                failed.append(off)

    if failed:
        _log(f"INCOMPLETE: {len(failed)} pages still failing: {sorted(failed)}. "
             f"Re-run to retry just those (cached pages are skipped).")
        sys.exit(1)

    # combine in offset order
    feats = []
    for off in offsets:
        with open(os.path.join(workdir, f"p{off:07d}.json")) as fh:
            feats.extend(json.load(fh).get("features", []) or [])
    with open(args.out, "w") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh)
    _log(f"WROTE {args.out} features={len(feats)}")


if __name__ == "__main__":
    main()
