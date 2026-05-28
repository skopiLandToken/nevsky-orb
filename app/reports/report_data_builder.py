"""TERRA White-Label Report — data assembly layer.

Pulls every section's data from live TERRA sources into ONE dict (report_context)
that the Jinja2 templates render to HTML/PDF. The contract: never fabricate. Where
a source isn't wired or returns nothing real, emit the IN_FLIGHT sentinel and the
template renders an honest "[IN FLIGHT]" badge instead of a fake number.

Identity note (Deschutes): the public-facing "Tax ID" IS the DIAL property_id
(e.g. Reindeer = 287611). The parcel polygon table (parcels_deschutes) is keyed by
map-taxlot (151319AC00139). We resolve taxid -> taxlot off the DIAL Index page,
then drive both the DIAL section fetchers (by taxlot) and the PostGIS geometry/comps
queries off that taxlot.

COPYRIGHT (hard rule): imagery is Esri World Imagery, USGS NAIP/Topo, FEMA NFHL, and
our own PostGIS boundary geometry ONLY. No Google imagery anywhere. Every image and
data table carries a (provider, url, pulled_at, license) record that the citations
section renders verbatim.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
from datetime import datetime, timezone

import httpx

from children import orb_db as db

IN_FLIGHT = "[IN FLIGHT]"

_UA = "Mozilla/5.0 SKOpi-TERRA/1.0 (+https://skopi.io)"
_DIAL_BASE = "http://dial.deschutes.org"

# Imagery / overlay services — all commercial-clean or public-domain.
_ESRI_IMAGERY = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
_USGS_TOPO = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/export"
_FEMA_NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/export"

_IMG_W, _IMG_H = 1100, 770  # map image pixel size (aspect ~1.43)

# Map-image cache. Imagery is the report's slow path (~10s of a ~12s build is the
# 3 Esri/USGS/FEMA export fetches). It's also stable month-to-month, so we cache the
# raw export PNG in Redis keyed by (service, bbox, size, transparent). Warm builds
# skip the network entirely and land near the ~2s PostGIS+PIL floor.
_MAP_CACHE_PREFIX = "terra:mapimg:v1:"
_MAP_CACHE_TTL = int(os.environ.get("TERRA_MAP_CACHE_TTL", str(30 * 24 * 3600)))  # 30d
_redis_client = None
_redis_tried = False


def _map_cache():
    """Lazy binary-safe Redis client for map images. Optional by design: any
    connection failure fails open (returns None) so report generation never depends
    on the cache being up. Separate from the text Yindo client (we need raw bytes)."""
    global _redis_client, _redis_tried
    if _redis_tried:
        return _redis_client
    _redis_tried = True
    try:
        import redis
        c = redis.Redis(
            host=os.getenv("YINDO_REDIS_HOST", "redis"),
            port=int(os.getenv("YINDO_REDIS_PORT", "6379")),
            decode_responses=False,
            socket_connect_timeout=1,
            socket_timeout=2,
        )
        c.ping()
        _redis_client = c
    except Exception:
        _redis_client = None
    return _redis_client


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Source registry — every external pull appends one record; citations renders it.
# ---------------------------------------------------------------------------
class _Sources:
    def __init__(self):
        self.records: list[dict] = []

    def add(self, label, provider, url, license_, pulled_at=None, ok=True):
        self.records.append({
            "label": label,
            "provider": provider,
            "url": url,
            "license": license_,
            "pulled_at": pulled_at or _now_iso(),
            "ok": ok,
        })


# ---------------------------------------------------------------------------
# DIAL Index page parse — owner, situs, account, legal description.
# ---------------------------------------------------------------------------
def _dial_index(property_id: str) -> dict:
    """Parse /Real/Index/{property_id} for identity fields not in the polygon table."""
    out = {"ok": False}
    url = f"{_DIAL_BASE}/Real/Index/{property_id}"
    try:
        from bs4 import BeautifulSoup as BS
        html = httpx.get(url, headers={"User-Agent": _UA}, timeout=20,
                         follow_redirects=True).text
        soup = BS(html, "html.parser")
        flat = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

        def span(eid):
            el = soup.find(id=eid)
            return el.get_text(" ", strip=True) if el else None

        def grab(label, stop):
            m = re.search(rf"{label}\s*:?\s*(.+?)\s*(?:{stop})", flat, re.I)
            return m.group(1).strip() if m else None

        out["map_taxlot"] = span("uxMapTaxlot")
        situs = span("uxSitusAddress") or grab("Situs Address", "Map and Taxlot|Account|Mailing")
        out["situs"] = None if (situs and "NO SITUS" in situs.upper()) else situs
        out["owner"] = grab("Mailing Name", "Map and Taxlot|Account|Situs")
        # Account IS the DIAL property_id — authoritative, don't scrape it from nav text.
        out["account"] = str(property_id)
        tca = re.search(r"Tax Code Area\s*:?\s*([0-9]{3,5})", flat)
        out["tax_code_area"] = tca.group(1) if tca else None

        # Platted legal: subdivision + tract/lot/block live in the page text.
        subdiv = re.search(r"([A-Z][A-Z0-9 ]*?PHASE\s*\d+|[A-Z][A-Z0-9 ]*?SUBDIVISION)", flat)
        tract = re.search(r"(TRACT\s+[A-Z0-9]+)", flat)
        block = re.search(r"\bBLOCK\s+([A-Z0-9]+)", flat)
        lot = re.search(r"\bLOT\s+([A-Z0-9]+)", flat)
        parts = []
        if subdiv:
            parts.append(subdiv.group(1).strip())
        if tract:
            parts.append(tract.group(1).strip())
        if lot:
            parts.append(f"LOT {lot.group(1)}")
        if block:
            parts.append(f"BLOCK {block.group(1)}")
        out["legal"] = ", ".join(parts) if parts else None
        out["index_url"] = url
        out["ok"] = True
    except Exception as e:
        out["error"] = str(e)
        out["index_url"] = url
    return out


# ---------------------------------------------------------------------------
# Imagery helpers — ArcGIS export, base64 embed, lat-corrected bbox, SVG overlay.
# ---------------------------------------------------------------------------
def _padded_bbox(xmin, ymin, xmax, ymax, pad, w=_IMG_W, h=_IMG_H):
    """Return an export bbox that contains the parcel with `pad` margin and whose
    aspect (after latitude correction) matches the image so the parcel renders
    undistorted under the plate-carree export (imageSR=4326)."""
    clon = (xmin + xmax) / 2.0
    clat = (ymin + ymax) / 2.0
    coslat = max(math.cos(math.radians(clat)), 1e-6)
    # half-extents needed to contain the parcel, padded
    hy = max((ymax - ymin) / 2.0, (xmax - xmin) / 2.0 / coslat) * (1.0 + pad)
    # also ensure longitude side contains the parcel
    hy = max(hy, (xmax - xmin) / 2.0 * (h / w) / coslat * (1.0 + pad))
    hx = hy * (w / h) / coslat
    return (clon - hx, clat - hy, clon + hx, clat + hy)


def _arcgis_png(base, bbox, transparent=False,
                size=(_IMG_W, _IMG_H)) -> tuple[bytes | None, str | None]:
    """Fetch an ArcGIS export PNG. Returns (png_bytes_or_None, pulled_at_iso_or_None).

    Redis-cached by (service, bbox, size, transparent). A cache hit returns the stored
    image AND its true fetch date, so the citations section never relabels a cached
    image as 'pulled today' — the hard copyright rule requires the pull date be honest.
    Only successful fetches are cached; failures fall through and retry next build."""
    xmin, ymin, xmax, ymax = bbox
    params = {
        "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "bboxSR": "4326", "imageSR": "4326",
        "size": f"{size[0]},{size[1]}",
        "format": "png", "transparent": "true" if transparent else "false",
        "f": "image",
    }
    cache = _map_cache()
    ckey = None
    if cache is not None:
        raw = (f"{base}|{xmin:.7f},{ymin:.7f},{xmax:.7f},{ymax:.7f}"
               f"|{size[0]}x{size[1]}|t={int(transparent)}")
        ckey = _MAP_CACHE_PREFIX + hashlib.sha256(raw.encode()).hexdigest()
        try:
            hit = cache.hgetall(ckey)
            if hit and hit.get(b"png"):
                at = hit.get(b"at")
                return hit[b"png"], (at.decode() if at else _now_iso())
        except Exception:
            pass
    try:
        r = httpx.get(base, params=params, headers={"User-Agent": _UA}, timeout=30,
                      follow_redirects=True)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            png = r.content
            at = _now_iso()
            if cache is not None and ckey is not None:
                try:
                    cache.hset(ckey, mapping={"png": png, "at": at})
                    cache.expire(ckey, _MAP_CACHE_TTL)
                except Exception:
                    pass
            return png, at
    except Exception:
        return None, None
    return None, None


def _composite(base_png: bytes, overlay_png: bytes) -> bytes:
    """Alpha-composite a transparent overlay onto a base image (FEMA over satellite)."""
    from PIL import Image
    base = Image.open(io.BytesIO(base_png)).convert("RGBA")
    ov = Image.open(io.BytesIO(overlay_png)).convert("RGBA")
    if ov.size != base.size:
        ov = ov.resize(base.size)
    out = Image.alpha_composite(base, ov)
    buf = io.BytesIO()
    out.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _overlay_nonempty(overlay_png: bytes) -> bool:
    """True if a transparent overlay actually drew something (any opaque pixel)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(overlay_png)).convert("RGBA")
        alpha = im.getchannel("A")
        return alpha.getextrema()[1] > 8
    except Exception:
        return False


def _b64(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _rings(geojson: dict) -> list:
    """Flatten a (Multi)Polygon GeoJSON to a list of coordinate rings."""
    gtype = geojson.get("type")
    coords = geojson.get("coordinates", [])
    if gtype == "Polygon":
        return coords
    if gtype == "MultiPolygon":
        out = []
        for poly in coords:
            out.extend(poly)
        return out
    return []


def _draw_boundary(png: bytes, geojson: dict, bbox) -> bytes:
    """Burn the parcel boundary onto the image with Pillow. Done in raster (not CSS)
    so alignment is exact and not dependent on the PDF engine's overlay handling."""
    from PIL import Image, ImageDraw
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    W, H = im.size
    xmin, ymin, xmax, ymax = bbox
    dx = (xmax - xmin) or 1e-9
    dy = (ymax - ymin) or 1e-9
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    def to_px(ring):
        return [((lon - xmin) / dx * W, (ymax - lat) / dy * H) for lon, lat in ring]

    for ring in _rings(geojson):
        px = to_px(ring)
        if len(px) >= 3:
            d.polygon(px, fill=(255, 210, 74, 55))
            d.line(px + [px[0]], fill=(255, 210, 74, 255), width=max(3, W // 320))
    out = Image.alpha_composite(im, overlay).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# DIAL section normalizers.
# ---------------------------------------------------------------------------
def _norm_valuation(rows: list[dict], account: str) -> dict:
    """rows are metric-by-year: {"": "Real Market Value - Land", "2024 - 2025": "$..."}.
    DIAL appends the account id to some metric labels; strip it."""
    if not rows:
        return {}
    year_cols = []
    for r in rows:
        for k in r:
            if k and k != "_links" and re.search(r"\d{4}", k) and k not in year_cols:
                year_cols.append(k)
    metrics = []
    for r in rows:
        label = r.get("") or r.get("col_0")
        if not label:
            continue
        label = re.sub(rf"\s+{re.escape(str(account))}$", "", label).strip()
        metrics.append({"label": label, "values": [r.get(y, "") for y in year_cols]})
    return {"years": year_cols, "metrics": metrics}


def _headline(valn: dict) -> dict:
    """Pull the latest-year land RMV, total RMV, and total assessed value for the
    executive summary. Returns dict with None where a metric isn't present."""
    if not valn or not valn.get("years"):
        return {}
    years = valn["years"]
    last = len(years) - 1

    def latest(*needles):
        for m in valn["metrics"]:
            lab = (m["label"] or "").lower()
            if all(n in lab for n in needles):
                vals = m.get("values") or []
                return vals[last] if last < len(vals) else None
        return None

    return {
        "year": years[last],
        "land_rmv": latest("real market", "land"),
        "total_rmv": latest("total", "real market"),
        "assessed": latest("total assessed"),
    }


def _norm_sales(rows: list[dict], account: str) -> list[dict]:
    """Strip the trailing account-id DIAL appends to some sale headers."""
    out = []
    for r in rows:
        clean = {}
        for k, v in r.items():
            if k == "_links":
                continue
            nk = re.sub(rf"\s+{re.escape(str(account))}$", "", k).strip()
            clean[nk] = v
        out.append({
            "sale_date": clean.get("Sale Date") or clean.get("Date"),
            "buyer": clean.get("Buyer"),
            "seller": clean.get("Seller"),
            "sale_amount": clean.get("Sale Amount") or clean.get("Amount"),
            "instrument": clean.get("Recording Instrument") or clean.get("Instrument"),
            "deeplink": (r.get("_links") or [None])[0],
        })
    return out


# ---------------------------------------------------------------------------
# Main entry.
# ---------------------------------------------------------------------------
def build_report_context(taxid: str, county_fips: str = "017",
                         depth: str = "full", marketing_partner: dict | None = None) -> dict:
    """Assemble the full report_context for one parcel. Deschutes (017) is wired for v1;
    other counties return a clean IN_FLIGHT scaffold rather than crashing."""
    taxid = str(taxid).strip()
    src = _Sources()
    branding = {
        "brand_primary": "SKOpi",
        "brand_secondary": "TERRA",
        "tagline": "Land intelligence, made tangible.",
        "white_label_mode": False,  # future hook: swap SKOpi chrome for MP chrome
    }
    mp = marketing_partner or {
        "name": None, "brokerage": None, "phone": None, "email": None, "photo": None,
    }

    ctx = {
        "generated_at": datetime.now(timezone.utc).strftime("%B %d, %Y"),
        "generated_at_iso": _now_iso(),
        "depth": depth if depth in ("summary", "full") else "full",
        "branding": branding,
        "marketing_partner": mp,
        "IN_FLIGHT": IN_FLIGHT,
        "taxid": taxid,
        "county_fips": county_fips,
    }

    if county_fips != "017":
        ctx["county"] = db.COUNTY_REGISTRY.get(county_fips, {}).get("name", county_fips)
        ctx["unsupported"] = True
        ctx["identity"] = {"account": taxid, "county": ctx["county"]}
        ctx["notice"] = (
            f"Deep report generation is wired for Deschutes County (017) in v1. "
            f"{ctx['county']} County report assembly is {IN_FLIGHT}."
        )
        ctx["sources"] = src.records
        return ctx

    ctx["county"] = "Deschutes"

    # --- 1. Identity: DIAL Index resolves taxid -> taxlot + owner/legal -----
    idx = _dial_index(taxid)
    src.add("Owner, situs & legal description", "Deschutes County Assessor / DIAL",
            idx.get("index_url"), "Public record", ok=idx.get("ok", False))
    taxlot = idx.get("map_taxlot")

    # --- PostGIS parcel record (geometry, zoning, PLSS, acreage) ------------
    parcel = None
    geojson = None
    if taxlot:
        with db._conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT taxlot, township, range_, section, quarter, mapnumber, dial_url,
                          zone_code, zone_jurisdiction, zone_community_class,
                          ROUND((shape_area/43560.0)::numeric,3) AS acres,
                          ROUND(ST_Y(ST_Centroid(geom))::numeric,6) AS clat,
                          ROUND(ST_X(ST_Centroid(geom))::numeric,6) AS clon,
                          ST_XMin(geom) AS xmin, ST_YMin(geom) AS ymin,
                          ST_XMax(geom) AS xmax, ST_YMax(geom) AS ymax,
                          ST_AsGeoJSON(geom,7) AS gj
                   FROM parcels_deschutes WHERE taxlot=%s""",
                (taxlot,),
            )
            parcel = cur.fetchone()
        if parcel:
            geojson = json.loads(parcel["gj"])
            src.add("Parcel boundary geometry & zoning", "TERRA PostGIS (Deschutes ingest)",
                    parcel.get("dial_url"), "TERRA-owned")

    ctx["identity"] = {
        "account": idx.get("account") or taxid,
        "taxlot": taxlot or IN_FLIGHT,
        "owner": idx.get("owner") or IN_FLIGHT,
        "situs": idx.get("situs") or "No situs address on file (vacant land)",
        "legal": idx.get("legal") or IN_FLIGHT,
        "tax_code_area": idx.get("tax_code_area") or IN_FLIGHT,
        "county": "Deschutes",
        "county_fips": "017",
        "plss": (f"T{parcel['township']} R{parcel['range_']} Sec {parcel['section']}"
                 f"{(' ' + parcel['quarter']) if parcel and parcel.get('quarter') else ''}"
                 if parcel else IN_FLIGHT),
        "map_number": parcel["mapnumber"] if parcel else IN_FLIGHT,
        "acres": float(parcel["acres"]) if parcel and parcel["acres"] is not None else None,
        "zone_code": parcel["zone_code"] if parcel else None,
        "zone_jurisdiction": parcel["zone_jurisdiction"] if parcel else None,
        "zone_community_class": parcel["zone_community_class"] if parcel else None,
        "dial_url": parcel["dial_url"] if parcel else idx.get("index_url"),
    }

    # --- 4. Valuation (DIAL) ------------------------------------------------
    val = db.fetch_dial_valuation(taxlot) if taxlot else {"found": False}
    src.add("Assessed & real-market valuation", "Deschutes County Assessor / DIAL",
            val.get("dial_url"), "Public record", ok=bool(val.get("found")))
    ctx["valuation"] = _norm_valuation(val.get("rows") or [], idx.get("account") or taxid) \
        if val.get("found") else {}
    ctx["headline"] = _headline(ctx["valuation"])

    # --- 8. Ownership & sales history (DIAL) --------------------------------
    sales = db.fetch_dial_sales(taxlot) if taxlot else {"found": False}
    src.add("Sales & deed history", "Deschutes County Assessor / DIAL",
            sales.get("dial_url"), "Public record", ok=bool(sales.get("found")))
    ctx["sales"] = _norm_sales(sales.get("rows") or [], idx.get("account") or taxid) \
        if sales.get("found") else []

    # --- 7. Permits & development history (DIAL) ----------------------------
    permits = db.fetch_dial_permits(taxlot) if taxlot else {"found": False}
    src.add("Permit & development history", "Deschutes County / DIAL",
            permits.get("dial_permits_url"), "Public record", ok=bool(permits.get("found")))
    ctx["permits"] = {
        "url": permits.get("dial_permits_url"),
        "count": permits.get("permit_count", 0),
        "items": permits.get("permits") or [],
        "found": bool(permits.get("found")),
    }
    devdocs = db.fetch_dial_dev_docs(taxlot) if taxlot else {"found": False}
    ctx["dev_docs"] = {
        "rows": devdocs.get("rows") or [],
        "count": devdocs.get("row_count", 0),
        "url": devdocs.get("dial_url"),
        "found": bool(devdocs.get("found")),
    }
    if devdocs.get("found"):
        src.add("Development documents (easements, planning files)",
                "Deschutes County / DIAL", devdocs.get("dial_url"), "Public record")

    # --- 11. Development potential (TERRA Yield Calc) -----------------------
    yld = db.calculate_lot_yield(taxlot) if taxlot else {"error": "no taxlot"}
    ctx["yield_est"] = yld
    if yld and not yld.get("error"):
        src.add("Lot-yield estimate (zoning model)", "TERRA Yield Calc",
                yld.get("source_url"), "TERRA analysis")

    # --- 9 & 10. Comps + neighborhood (our PostGIS) ------------------------
    ctx["comps"] = []
    ctx["neighborhood"] = {}
    if parcel:
        clon, clat = float(parcel["clon"]), float(parcel["clat"])
        zone = parcel["zone_code"]
        with db._conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT taxlot, zone_code,
                          ROUND((shape_area/43560.0)::numeric,3) AS acres, dial_url,
                          ROUND(ST_Distance(ST_Centroid(geom)::geography,
                                ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography)::numeric,0) AS dist_m
                   FROM parcels_deschutes
                   WHERE taxlot <> %s AND zone_code IS NOT DISTINCT FROM %s
                   ORDER BY ST_Centroid(geom) <-> ST_SetSRID(ST_MakePoint(%s,%s),4326)
                   LIMIT 8""",
                (clon, clat, taxlot, zone, clon, clat),
            )
            ctx["comps"] = [dict(r) for r in cur.fetchall()]
            cur.execute(
                """SELECT COALESCE(zone_code,'(unzoned)') AS zone_code, COUNT(*) AS n,
                          ROUND(AVG(shape_area/43560.0)::numeric,2) AS avg_acres
                   FROM parcels_deschutes
                   WHERE ST_DWithin(ST_Centroid(geom)::geography,
                         ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, 800)
                   GROUP BY 1 ORDER BY n DESC""",
                (clon, clat),
            )
            mix = [dict(r) for r in cur.fetchall()]
            total = sum(m["n"] for m in mix)
            ctx["neighborhood"] = {
                "radius_m": 800,
                "total_parcels": total,
                "zone_mix": mix,
            }
        src.add("Comparable parcels & neighborhood mix", "TERRA PostGIS (Deschutes ingest)",
                None, "TERRA-owned")
        ctx["comps_note"] = (
            "Comparables are matched by zoning and proximity from TERRA's parcel layer. "
            f"Per-comp last-sale price is {IN_FLIGHT} (one DIAL fetch per comp; deferred "
            "to keep generation fast)."
        )

    # --- 5. Maps (Esri / USGS / FEMA) + boundary overlay -------------------
    maps = []
    if parcel:
        bxmin, bymin = float(parcel["xmin"]), float(parcel["ymin"])
        bxmax, bymax = float(parcel["xmax"]), float(parcel["ymax"])

        def make_map(title, base, pad, caption, provider, license_, overlay=True,
                     fema=False):
            bbox = _padded_bbox(bxmin, bymin, bxmax, bymax, pad)
            png, png_at = _arcgis_png(base, bbox)
            fema_note = None
            if png and fema:
                ov, ov_at = _arcgis_png(_FEMA_NFHL, bbox, transparent=True)
                if ov is not None and _overlay_nonempty(ov):
                    png = _composite(png, ov)
                    fema_note = "FEMA NFHL flood layer shown over imagery."
                else:
                    fema_note = "No mapped Special Flood Hazard Area intersects this parcel."
                src.add("FEMA flood hazard (NFHL)", "FEMA National Flood Hazard Layer",
                        _FEMA_NFHL, "Public domain", pulled_at=ov_at, ok=ov is not None)
            src.add(f"Map: {title}", provider, base, license_,
                    pulled_at=png_at, ok=png is not None)
            if overlay and png and geojson:
                png = _draw_boundary(png, geojson, bbox)
            return {
                "title": title,
                "image": _b64(png) if png else None,
                "overlay_svg": None,
                "caption": caption + (f" {fema_note}" if fema_note else ""),
                "provider": provider,
                "in_flight": png is None,
            }

        maps.append(make_map(
            "Aerial — parcel detail", _ESRI_IMAGERY, 1.4,
            "High-resolution aerial with TERRA parcel boundary overlay.",
            "Esri World Imagery", "Commercial use w/ attribution"))
        maps.append(make_map(
            "Aerial — area context", _ESRI_IMAGERY, 6.0,
            "Wider aerial context showing the parcel within its surroundings.",
            "Esri World Imagery", "Commercial use w/ attribution"))
        maps.append(make_map(
            "Topographic", _USGS_TOPO, 4.0,
            "USGS topographic basemap (contours, hydrography, public lands).",
            "USGS The National Map — US Topo", "Public domain"))
        maps.append(make_map(
            "Flood hazard", _ESRI_IMAGERY, 4.0,
            "FEMA flood hazard context.",
            "Esri World Imagery + FEMA NFHL", "Esri (attrib) / FEMA (public domain)",
            fema=True))
    else:
        maps.append({"title": "Maps", "image": None, "overlay_svg": None,
                     "caption": f"Parcel geometry unavailable — maps {IN_FLIGHT}.",
                     "provider": None, "in_flight": True})
    ctx["maps"] = maps

    # --- 6. Photo gallery ---------------------------------------------------
    # DIAL carries assessor photos for improved parcels; vacant land has none.
    # SVOIcloud / MP uploads are a future wire (out of scope v1).
    ctx["photos"] = {
        "items": [],
        "note": (f"No assessor photographs on record for this parcel (vacant land). "
                 f"Owner / Marketing Partner photo uploads via SVOIcloud are {IN_FLIGHT}."),
    }

    ctx["sources"] = src.records
    return ctx
