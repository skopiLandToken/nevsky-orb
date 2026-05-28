"""
child_engine — Shared execution engine for all ORB children.

Each child is a row in child_personas. This engine loads that row, applies
tier-based tool filtering, runs the Anthropic tool-use loop with rate-limit
fallback, and returns a reply.

Doctrine:
- Tier defines tool access. Override per-child via enabled_tools/disabled_tools.
- Sonnet 4.6 is default; rate-limit triggers Haiku fallback.
- All errors get sanitized before user-facing return.
- Child_engine NEVER writes through orb_db (read-only by design).
"""
import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional
import psycopg
from psycopg.rows import dict_row
from anthropic import Anthropic

from .orb_db import (
    search_knowledge_store,
    get_knowledge_entry_full,
    get_active_users,
    get_tombstoned_users,
    get_blocklist,
    get_honeypot_hits,
    get_recent_yakov_tasks,
    get_site_plans,
    get_ai_spend_today,
    lookup_parcel_by_point,
    fetch_dial_permits,
    fetch_dial_sales,
    fetch_dial_valuation,
    fetch_dial_dev_docs,
    fetch_multco_proptax,
    fetch_multco_records,
    fetch_multco_sail,
    fetch_multnomah_permits,
    fetch_crook_assessment,
    fetch_crook_permits,
    fetch_county_permits,
    fetch_marion_assessment,
    fetch_marion_records,
    fetch_marion_permits,
    fetch_lane_assessment,
    fetch_lane_records,
    fetch_lane_permits,
    fetch_jackson_assessment,
    fetch_jackson_records,
    fetch_jackson_permits,
    fetch_wash_assessment,
    fetch_wash_records,
    fetch_washington_permits,
    fetch_clack_assessment,
    fetch_clack_records,
    fetch_clackamas_permits,
    fetch_linn_assessment,
    fetch_linn_records,
    fetch_linn_permits,
    fetch_benton_assessment,
    fetch_benton_records,
    fetch_benton_permits,
    fetch_yamhill_assessment,
    fetch_yamhill_records,
    fetch_yamhill_permits,
    fetch_polk_assessment,
    fetch_polk_records,
    fetch_polk_permits,
    fetch_douglas_assessment,
    fetch_douglas_records,
    fetch_douglas_permits,
    fetch_josephine_assessment,
    fetch_josephine_records,
    fetch_josephine_permits,
    fetch_clatsop_assessment,
    fetch_clatsop_records,
    fetch_clatsop_permits,
    fetch_tillamook_assessment,
    fetch_tillamook_records,
    fetch_tillamook_permits,
    fetch_lincoln_assessment,
    fetch_lincoln_records,
    fetch_lincoln_permits,
    query_yakov_handoffs,
    query_yakov_commits,
    fetch_jefferson_assessment,
    fetch_jefferson_records,
    fetch_jefferson_permits,
    calculate_lot_yield,
)

logger = logging.getLogger(__name__)

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=30.0, max_retries=0)

MAX_TOOL_ITERATIONS = 4
MAX_TOOL_RESULT_CHARS = 2000


# =============================================================================
# TIER-TO-TOOL REGISTRY
# =============================================================================
# Lesson for Nevsky: this dict is THE doctrine for what each tier can do.
# Editing it changes authority across the entire system. Treat with care.
# Per-child override is possible via child_personas.enabled_tools / disabled_tools.
# =============================================================================

TIER_TOOLS = {
    "sovereign": [
        # Sophia only — full read across everything.
        "search_knowledge_store", "get_knowledge_entry_full",
        "get_active_users", "get_tombstoned_users",
        "get_blocklist", "get_honeypot_hits",
        "get_recent_yakov_tasks", "get_site_plans",
        "get_ai_spend_today",
        "lookup_parcel_by_point",
        "fetch_jefferson_assessment",
        "fetch_jefferson_records",
        "fetch_jefferson_permits",
        "fetch_dial_permits",
        "fetch_dial_sales",
        "fetch_dial_valuation",
        "fetch_dial_dev_docs",
        "fetch_multco_proptax",
        "fetch_multco_records",
        "fetch_multco_sail",
        "fetch_multnomah_permits",
        "fetch_crook_assessment",
        "fetch_crook_permits",
        "fetch_county_permits",
        "fetch_marion_assessment",
        "fetch_marion_records",
        "fetch_marion_permits",
        "fetch_lane_assessment",
        "fetch_lane_records",
        "fetch_lane_permits",
        "fetch_jackson_assessment",
        "fetch_jackson_records",
        "fetch_jackson_permits",
        "fetch_wash_assessment",
        "fetch_wash_records",
        "fetch_washington_permits",
        "fetch_clack_assessment",
        "fetch_clack_records",
        "fetch_clackamas_permits",
        "fetch_linn_assessment",
        "fetch_linn_records",
        "fetch_linn_permits",
        "fetch_benton_assessment",
        "fetch_benton_records",
        "fetch_benton_permits",
        "fetch_yamhill_assessment",
        "fetch_yamhill_records",
        "fetch_yamhill_permits",
        "fetch_polk_assessment",
        "fetch_polk_records",
        "fetch_polk_permits",
        "fetch_douglas_assessment",
        "fetch_douglas_records",
        "fetch_douglas_permits",
        "fetch_josephine_assessment",
        "fetch_josephine_records",
        "fetch_josephine_permits",
        "fetch_clatsop_assessment",
        "fetch_clatsop_records",
        "fetch_clatsop_permits",
        "fetch_tillamook_assessment",
        "fetch_tillamook_records",
        "fetch_tillamook_permits",
        "fetch_lincoln_assessment",
        "fetch_lincoln_records",
        "fetch_lincoln_permits",
        "query_yakov_handoffs",
        "query_yakov_commits",
        "calculate_lot_yield",
        "web_search",
    ],
    "executive": [
        # C-suite (Ophelia, future C-suite). Cant see honeypot or full users list.
        "search_knowledge_store", "get_knowledge_entry_full",
        "get_active_users",
        "get_recent_yakov_tasks", "get_site_plans",
        "lookup_parcel_by_point",
        "fetch_jefferson_assessment",
        "fetch_jefferson_records",
        "fetch_jefferson_permits",
        "fetch_dial_permits",
        "fetch_dial_sales",
        "fetch_dial_valuation",
        "fetch_dial_dev_docs",
        "fetch_multco_proptax",
        "fetch_multco_records",
        "fetch_multco_sail",
        "fetch_multnomah_permits",
        "fetch_crook_assessment",
        "fetch_crook_permits",
        "fetch_county_permits",
        "fetch_marion_assessment",
        "fetch_marion_records",
        "fetch_marion_permits",
        "fetch_lane_assessment",
        "fetch_lane_records",
        "fetch_lane_permits",
        "fetch_jackson_assessment",
        "fetch_jackson_records",
        "fetch_jackson_permits",
        "fetch_wash_assessment",
        "fetch_wash_records",
        "fetch_washington_permits",
        "fetch_clack_assessment",
        "fetch_clack_records",
        "fetch_clackamas_permits",
        "fetch_linn_assessment",
        "fetch_linn_records",
        "fetch_linn_permits",
        "fetch_benton_assessment",
        "fetch_benton_records",
        "fetch_benton_permits",
        "fetch_yamhill_assessment",
        "fetch_yamhill_records",
        "fetch_yamhill_permits",
        "fetch_polk_assessment",
        "fetch_polk_records",
        "fetch_polk_permits",
        "fetch_douglas_assessment",
        "fetch_douglas_records",
        "fetch_douglas_permits",
        "fetch_josephine_assessment",
        "fetch_josephine_records",
        "fetch_josephine_permits",
        "fetch_clatsop_assessment",
        "fetch_clatsop_records",
        "fetch_clatsop_permits",
        "fetch_tillamook_assessment",
        "fetch_tillamook_records",
        "fetch_tillamook_permits",
        "fetch_lincoln_assessment",
        "fetch_lincoln_records",
        "fetch_lincoln_permits",
        "calculate_lot_yield",
        "web_search",
    ],
    "staff": [
        # Internal team. Can search KB but not see system internals.
        "search_knowledge_store", "get_knowledge_entry_full",
        "web_search",
    ],
    "partner": [
        # Affiliates, Wade, external collaborators. KB only what is public/their scope.
        "search_knowledge_store",
        "web_search",
    ],
    "public": [
        # Outward-facing (Vesna social media). No internal data.
        "web_search",
    ],
    "honeypot": [
        # Decommissioned children. NO tools.
    ],
}


# =============================================================================
# TOOL DEFINITIONS — Anthropic API format
# =============================================================================

ALL_TOOL_SCHEMAS = {
    "web_search": {
        "type": "web_search_20250305",
        "name": "web_search",
    },
    "search_knowledge_store": {
        "name": "search_knowledge_store",
        "description": "Search Nevsky knowledge_store for entries by free-text query, content_type, and/or tags.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "content_type": {"type": "string", "enum": ["agreement", "session_note", "decision", "email", "document", "playbook", "profile", "research", "other"]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 10}
            }
        }
    },
    "get_knowledge_entry_full": {
        "name": "get_knowledge_entry_full",
        "description": "Retrieve FULL content of one knowledge_store entry by id.",
        "input_schema": {"type": "object", "properties": {"entry_id": {"type": "string"}}, "required": ["entry_id"]}
    },
    "get_active_users": {
        "name": "get_active_users",
        "description": "All non-tombstoned users in SKOpi.",
        "input_schema": {"type": "object", "properties": {}}
    },
    "get_tombstoned_users": {
        "name": "get_tombstoned_users",
        "description": "All tombstoned users with reasons.",
        "input_schema": {"type": "object", "properties": {}}
    },
    "get_blocklist": {
        "name": "get_blocklist",
        "description": "personas_blocklist - actors blocked across all bots.",
        "input_schema": {"type": "object", "properties": {}}
    },
    "get_honeypot_hits": {
        "name": "get_honeypot_hits",
        "description": "Recent contact attempts captured by the blocklist guard.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}
    },
    "get_recent_yakov_tasks": {
        "name": "get_recent_yakov_tasks",
        "description": "Recent automation tasks from yakov_tasks queue.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}}
    },
    "get_site_plans": {
        "name": "get_site_plans",
        "description": "Site plan analyses with lot counts and pricing.",
        "input_schema": {"type": "object", "properties": {}}
    },
    "get_ai_spend_today": {
        "name": "get_ai_spend_today",
        "description": "Today AI spend across all bots and Yakov, by model.",
        "input_schema": {"type": "object", "properties": {}}
    },
    "lookup_parcel_by_point": {
        "name": "lookup_parcel_by_point",
        "description": "TERRA-FIELD-6c MAX HYPE. Look up the county parcel containing a given latitude/longitude across all TERRA-covered counties (currently Deschutes + Crook + Multnomah; Jefferson + Lane arriving). Use when the user shares a location pin or asks about a parcel at coordinates. Returns: taxlot, county (Deschutes/Crook), county_fips, section_id, acres, county_url (DIAL for Deschutes, PATS for Crook), map_number, owner_name (Crook native), situs_address (Crook native), zone_code, zone_label, parcel_centroid. CRITICAL: This is a SHOWCASE moment. The user is demoing TERRA to investors / real estate agents / partners. NO markdown tables (pipes render raw). Use bold *labels*, dramatic dividers, generous emoji storytelling.\n\nCOUNTY-AWARE RENDERING: Adapt the subtitle and direct-link line based on the `county` field returned. Crook lookups include owner_name + situs_address inline — surface those prominently. Deschutes lookups don't (use fetch_dial_* for those). Zoning is now LIVE in both Deschutes (resolved per parcel via PostGIS zoning layer) and Crook (native in parcel attributes) — show zone_code + zone_label, never frame zoning as IN FLIGHT.\n\nTEMPLATE — adapt with real data, keep the energy:\n\n✨🛰️ *TERRA SCAN COMPLETE* 🛰️✨\n🎆 _{county} County · Live Spatial Intelligence_ 🎆\n\n⭐━━━━━━━━━━━━━━━━━⭐\n\n🎯 *PARCEL LOCATED*\n🏷️ `{taxlot}`\n\n🗺️ *Section:* {section_id}\n🧭 *County Map:* {map_number}\n📐 *Footprint:* *{acres} acres* _({sqft:,} sq ft)_\n📍 *Centroid:* {lat}°N, {lon}°W\n\n[IF owner_name present:]\n👤 *Owner of Record:* {owner_name}\n🏠 *Situs:* {situs_address or '— vacant —'}\n\n⭐━━━━━━━━━━━━━━━━━⭐\n\n🏘️ *ZONING LAYER*\n📛 *Zone:* `{zone_code}` — {zone_label}\n\n🏗️ *DEVELOPMENT MATH*\n_Tap_ 💡 *YIELD CALC* _below for buildable-lot math, or just say it._\n\n⭐━━━━━━━━━━━━━━━━━⭐\n\n🔥 *PERMIT & ACTIVITY SCAN*\n_Live county permit pull is one tap away. Sophia will hit the county records system, parse the permit list, and tell you exactly whats been filed and whether anyone is actively developing this parcel._ 🚧\n\n⭐━━━━━━━━━━━━━━━━━⭐\n\n🔗 *DIRECT TO TRUTH*\n[For Deschutes:] Deschutes DIAL → {county_url}\n[For Crook:] Crook PATS → {county_url}\n\n💎 _— Sophia · Nevsky ORB · {county} Layer Active_ 💎\n\n👇 _Tap a button below to dig deeper, or just tell me what you want._\n\nTONE: CIA-grade land briefing in a tuxedo. Confident. Theatrical. TERRA SCAN COMPLETE should feel like a vault opening. When pieces are genuinely not built yet (Jefferson permits, Lane records, etc.), frame as IN FLIGHT — never apologize. The user is showing this to other people; make them look brilliant. Always end with one specific follow-up beyond the buttons.",
        "input_schema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "WGS84 latitude in decimal degrees, e.g., 44.2891"},
                "longitude": {"type": "number", "description": "WGS84 longitude in decimal degrees (negative for west), e.g., -121.1730"}
            },
            "required": ["latitude", "longitude"]
        }
    },
    "fetch_dial_permits": {
        "name": "fetch_dial_permits",
        "description": "TERRA: Fetch the live county permit list for a Deschutes parcel from the official DIAL system. Use when the user asks about permits, building activity, development status, or what's been filed on a specific taxlot. Returns permit_count and a permits list with permit_id, permit_type (Building/Plumbing/Mechanical/Septic/Road Access/Land Use/etc), permit_name, application_date, status, and a deeplink to the full DIAL permit detail page. Results cached 24h. CRITICAL FORMATTING for Telegram: when summarizing permits, use the same MAX HYPE TERRA aesthetic — section header, emoji bullets per permit, group by type if many, mention the most recent activity prominently, and always conclude with whether the parcel appears actively developed (multiple permits in last 12 months) vs dormant. End with the dial_permits_url for full review. Tone: confident land-intelligence briefing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Deschutes taxlot ID, e.g., 151309AA01201"},
                "force_refresh": {"type": "boolean", "default": False, "description": "Bypass 24h cache and re-fetch from DIAL. Use sparingly."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_dial_sales": {
        "name": "fetch_dial_sales",
        "description": "TERRA: Fetch the SALES HISTORY for a Deschutes parcel from DIAL. Use when the user asks about prior sales, prices, who owned it, or wants comp data on a taxlot. Returns rows with Sale Date, Seller, Buyer, Sale Amount. Most recent at top. Cached 24h. FORMATTING for Telegram: maintain TERRA aesthetic. Section header with 💰 emoji. List sales chronologically newest-first. Highlight the most recent sale prominently and call out if the parcel has been actively flipping vs held long-term. End with the dial_url for full review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string"},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_dial_valuation": {
        "name": "fetch_dial_valuation",
        "description": "TERRA: Fetch the multi-year ASSESSED VALUATION history for a Deschutes parcel. Use when the user asks about assessed value, taxable value, market value trends, or how the county has valued the property over time. Returns valuation rows broken out year-over-year. FORMATTING: TERRA aesthetic. 📊 emoji header. Show year-over-year trend. Call out big jumps or drops. End with dial_url.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string"},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_dial_dev_docs": {
        "name": "fetch_dial_dev_docs",
        "description": "TERRA: Fetch the DEVELOPMENT DOCUMENTS on file for a Deschutes parcel — easements, planning files, recorded encumbrances, BLM/BPA/utility agreements. THIS IS WHERE EASEMENTS LIVE. Critical for any due-diligence on a parcel before purchase or development. Returns rows with Date Uploaded, Document Type, Description, File Number, plus deeplinks to actual documents. FORMATTING: TERRA aesthetic. 📋 emoji header. Always flag the word EASEMENT or ENCUMBRANCE prominently if found. List documents newest-first. Call out anything that looks like a utility easement (BPA/Bonneville/Pacific Power/electric/transmission) as a high-priority finding. End with dial_url.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string"},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_multco_proptax": {
        "name": "fetch_multco_proptax",
        "description": "TERRA: Fetch the Multnomah County property snapshot for a taxlot — owner of record, mailing + situs addresses, current-roll valuation (land/imp/Measure-50 assessed), most-recent sale price + date, most-recent deed type + instrument number, zoning code, improvements (year built / main sqft / units), acreage. Use when the user asks about who owns a Portland parcel, what it sold for, what it's worth, zoning, or any property snapshot question on a Multnomah taxlot. Returns a rich payload built from the bulk taxlot service (Multnomah's L1 ships native owner + valuation, so this is a zero-upstream-call Sophia-fast snapshot). MARKS as IN FLIGHT: tax payment history and current bill status (MultcoPropTax.com is captcha-walled; paid Tyler API tier pending Iosif decision). FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 📍 SITUS / 📊 VALUATION / 💰 LAST SALE / 📋 LAST DEED / 🏘️ ZONING / 🏗️ IMPROVEMENTS). List IN FLIGHT items at end as 'coming soon' — never apologize. End with the source_url for manual deep-dive.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Multnomah taxlot ID (MAPTAXLOT format, e.g. '1N1E22BC  3500')."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Bypass 24h cache and re-pull from parcels_multnomah. Rarely needed since L1 reingests nightly."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_multco_records": {
        "name": "fetch_multco_records",
        "description": "TERRA: Fetch the Multnomah RECORDED DOCUMENT history for a taxlot — deeds, mortgages, liens, recorded since 2002. CURRENTLY IN FLIGHT: the upstream source (MultcoRecords.com Digital Research Room) is gated by Google reCAPTCHA v2, blocking automated retrieval. Returns the best-available recorded-instrument data from parcels_multnomah Layer 1 (most-recent deed type + date + instrument number, plus most-recent sale) and flags the full history as IN FLIGHT pending paid-tier authorization or alternative source. FORMATTING: TERRA aesthetic. 📜 emoji header. Surface the most-recent deed prominently. Frame the IN FLIGHT honestly — 'full chain of title since 2002 is coming once we wire the paid tier' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string"},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_multco_sail": {
        "name": "fetch_multco_sail",
        "description": "TERRA: Fetch Multnomah SAIL (Survey and Assessment Image Locator) cadastral imagery — surveys, recorded plats, cadastral maps. CURRENTLY IN FLIGHT: SAIL is not listed in the public Multnomah Assessment & Taxation catalog as of 2026-05-27 — may have been retired, renamed, or moved. Cadastral basics (map_id, township/range, assessor_map, legal_desc, tract_lot, block) are available in parcels_multnomah Layer 1. FORMATTING: TERRA aesthetic. 🗺️ emoji header. Frame as 'SAIL surface re-confirmation pending' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string"},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_multnomah_permits": {
        "name": "fetch_multnomah_permits",
        "description": "TERRA: Fetch the live PERMIT HISTORY for a Multnomah parcel from Portland's open-data ArcGIS feed (Building Permit Details, COP_OpenData_PlanningDevelopment Layer 1288). Spatial-intersects the parcel envelope to grab permits filed inside or touching the lot. Use when the user asks about permits, building activity, development status, or what's been filed on a specific Multnomah taxlot. COVERAGE: City of Portland parcels (~80% of Multnomah). Gresham + Multnomah unincorporated are IN FLIGHT (separate jurisdictions; need GreshamView + county direct, Phase 2). Returns permit_count and full permit attribute list. FORMATTING: TERRA aesthetic. 🚧 emoji header. Group permits by status or type. Call out anything filed in the last 12 months as 'active development' vs older permits as 'history.' If parcel is Gresham/unincorporated, frame as 'Portland city permits feed doesn't cover this jurisdiction — Phase 2 fetcher in flight.' End with the source_url.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Multnomah taxlot ID (MAPTAXLOT format)."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Currently unused — v1 hits Portland Maps live each call. Caching is a Phase 2 follow-up."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_crook_assessment": {
        "name": "fetch_crook_assessment",
        "description": "TERRA: Fetch the Crook County assessment + ownership snapshot for a taxlot. Use when the user asks about who owns a Crook parcel, what's on file, account info, prop class, zoning, or any property snapshot question on a Crook taxlot. Returns owner_name, situs + mailing addresses, account number, prop_class + description, tax_code_area, subdivision (if platted), assessed + GIS acreage, zoning code + label, plus verified deep links to the tax card PDF, tax map PDF, and PSO recorder. IN FLIGHT — flag these honestly, do not invent numbers: structured valuation (real-market / assessed / taxable), multi-year tax payment history, full sales chain. The legacy Crook tax cards are scanned image PDFs from 2013 — OCR pipeline is queued; until then the user gets the PDF link to read directly. The PSO recorder is a Blazor SPA that needs Playwright or alt source; deep link is provided. FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 📍 SITUS / 🧾 ACCOUNT / 🏘️ ZONING / 🌍 ACREAGE). List IN FLIGHT items at the end as 'coming soon' — never apologize. Always end with the tax_card_pdf direct link and the pso_recorder link so the broker can pull the source themselves.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Crook taxlot ID (e.g. '151605BB07500')."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Bypass 24h cache. Rarely needed since the snapshot is driven by L1 inline data."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_crook_permits": {
        "name": "fetch_crook_permits",
        "description": "TERRA: Fetch building permits for a Crook County parcel via Oregon's state ePermitting portal (Accela ACA). Convenience wrapper around fetch_county_permits with county_fips='013' pre-set. Use when the user asks about permits, building activity, development status on a Crook taxlot. IN FLIGHT — server-side scrape of the Accela result page is gated on viewstate-capture work; currently returns a verified deep link the user clicks through to run the search themselves (one search-submit reveals the live permit list). FORMATTING: TERRA aesthetic. 🚧 emoji header. Frame the deep link as 'one tap from the live state portal' and surface it prominently. Never apologize for the IN FLIGHT — frame as the scrape upgrade arriving.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Crook taxlot ID."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_county_permits": {
        "name": "fetch_county_permits",
        "description": "TERRA: Fetch building permits for ANY Oregon county that participates in the state ePermitting portal (Accela ACA). Reusable across Crook (FIPS 013), Jefferson (031), Lane (039), and other participating counties — pass the county_fips alongside the taxlot. Counties NOT in the state system (Deschutes uses DIAL; Multnomah uses Portland Maps; Washington uses its OWN Accela instance — call fetch_washington_permits) return a structured 'not_applicable' response routing the caller to the county-native fetcher. Use this when handling permits for any non-Deschutes, non-Multnomah, non-Washington Oregon parcel. CURRENT STATUS — same as fetch_crook_permits: returns the verified Accela deep link, scrape upgrade IN FLIGHT. FORMATTING: TERRA aesthetic. 🚧 emoji header. Branch on status — 'deep_link_only' surfaces the link, 'not_applicable' redirects to the right county-native fetcher.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full taxlot ID for the parcel."},
                "county_fips": {"type": "string", "description": "Three-digit county FIPS code without state prefix (e.g. '013' for Crook, '031' for Jefferson, '039' for Lane)."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot", "county_fips"]
        }
    },
    "fetch_marion_assessment": {
        "name": "fetch_marion_assessment",
        "description": "TERRA: Fetch the Marion County assessment + ownership snapshot for a taxlot (Salem, Keizer, Stayton, Aumsville, Silverton, Mt Angel, unincorporated Marion). Use when the user asks who owns a Marion parcel, what's on file, account info, prop class, zoning, current valuation, building info, or any property snapshot on a Marion taxlot. Marion's parcels service ships richer Layer-1 inline data than most Oregon counties — owner, situs, current RMV (land + improvement + total), assessed value, zoning code + authority + URL, year built + building area + living area, prop class + tax routing (tax code / city / school / fire), plus verified deep links to MCASR PropertySummary and the assessor tax-map PDF. IN FLIGHT — flag honestly, do not invent: multi-year tax payment history (MCASR PropertySummary.aspx is ASP.NET WebForms with __VIEWSTATE-gated data on the initial GET — same pattern as Crook's PSO; deep link returns the user one tap from the live history). FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 📍 SITUS / 🧾 ACCOUNT / 🏘️ ZONING / 💰 VALUATION / 🏗️ BUILDING / 🌍 ACREAGE). Highlight rmv_total in 💰 VALUATION. List IN FLIGHT items at the end as 'coming soon' — never apologize. Always end with the mcasr_property deep link and tax_map_pdf so the broker can pull the source themselves.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Marion taxlot ID (e.g. '073W27AA00200' for the State Capitol)."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Currently no-op — Marion L2 is a SQL pass-through over L1 inline; freshness comes from the daily L1 delta refresh."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_marion_records": {
        "name": "fetch_marion_records",
        "description": "TERRA: Fetch the Marion County deed / recorded-instrument snapshot for a taxlot. Use when the user asks about deeds, recent sale, transfer history, recorded documents, or 'what was the last instrument on this Marion parcel'. Returns the MOST-RECENT instrument-of-record (number, type, date, sale price), the current owner of record, plus a deep link to the Marion Clerk's Recordings page where the user can search the full document history. IN FLIGHT — Marion Clerk's public recordings portal URL is not yet located (the standalone DNS endpoints all 404 on probe); only the latest instrument lives in our Layer-1 snapshot, full multi-instrument chain is one click through the Clerk landing page. FORMATTING: TERRA aesthetic. 📜 emoji header. Lead with the latest_instrument (number / type / date / sale_price), then the owner_of_record, then the Clerk deep link. Frame the IN FLIGHT as 'full chain of title is one tap from the Clerk page' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Marion taxlot ID."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_marion_permits": {
        "name": "fetch_marion_permits",
        "description": "TERRA: Fetch building permits for a Marion County parcel via Oregon's state ePermitting portal (Accela ACA). Convenience wrapper around fetch_county_permits with county_fips='047' pre-set. Marion participates in the same state Accela portal as Crook/Jefferson/Lane — confirmed via the Marion PublicWorksPermits GIS service whose metadata names Accela as the source-of-record. Use when the user asks about permits, building activity, development status on a Salem / Keizer / unincorporated Marion taxlot. IN FLIGHT — same deep-link-only pattern as fetch_crook_permits and fetch_county_permits: server-side scrape of the Accela result page is gated on viewstate capture or Playwright; currently returns a verified deep link the user clicks through to run the search themselves. FORMATTING: TERRA aesthetic. 🚧 emoji header. Frame the deep link as 'one tap from the live state portal.' Surface the future-upgrade note where useful — Marion ALSO ships permits as point geometry via the county GIS service, which is queued as the richer Layer-3 source once the daily-poll permits worker lands.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Marion taxlot ID."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_lane_assessment": {
        "name": "fetch_lane_assessment",
        "description": "TERRA: Fetch the Lane County assessment + property snapshot for a taxlot. Use when the user asks about a parcel in Eugene / Springfield / Florence / Cottage Grove / Junction City / unincorporated Lane — anywhere in the Eugene metro or Lane County proper. Lane is the RICHEST Tier 1 L1 — owner, situs, FULL valuation (RMV land + improvements + personal, AV, taxable, certified tax annual), zoning code + plan designation, planning jurisdiction (EUG/SPR/LAN), urban growth boundary, incorporated city, school + fire district, census tract, improvement type + year built + sqft, neighborhood association, market area, and plat are ALL native at L1 — no upstream fetch needed for the snapshot. IN FLIGHT — flag honestly: multi-year tax payment history + per-bill status live behind apps.lanecounty.org A&T detail (JS-rendered); RLID full property report is subscription-gated (deep link surfaces the property summary regardless). Use the rlid_property_report deep link as the canonical 'see more' hand-off. FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 🏠 SITUS / 💰 VALUATION / 🏘️ ZONING & PLANNING / 🏗️ IMPROVEMENTS / 🌐 DISTRICTS / 🔗 DEEP LINKS). Surface valuation prominently — Lane gives us numbers Multnomah doesn't. End with the rlid_property_report link as the broker hand-off.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Lane taxlot ID (13-char maptaxlot format, e.g. '1704353200500')."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Bypass 24h cache and re-pull from parcels_lane."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_lane_records": {
        "name": "fetch_lane_records",
        "description": "TERRA: Fetch Lane County recorded documents (deeds, mortgages, liens, sales chain) for a taxlot. CURRENTLY IN FLIGHT — Lane County Clerk recordings office has no public queryable API as of 2026-05-27; RLID consolidates these but full reports are subscription-gated. Returns the most-recent owner from parcels_lane L1 inline data plus the canonical RLID property-report deep link and Lane Clerk recordings homepage. Honest IN FLIGHT framing — open paths are (a) RLID paid tier, (b) paid Tyler integration, (c) per-deed Clerk export. FORMATTING: TERRA aesthetic. 📜 emoji header. Surface current owner from L1 prominently. Frame the IN FLIGHT honestly — 'full chain of title arrives once we wire one of the paid paths' — never apologize, never fabricate. End with the rlid_property_report deep link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string"},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_lane_permits": {
        "name": "fetch_lane_permits",
        "description": "TERRA: Fetch building permits for a Lane County parcel. Routes by planning jurisdiction: Eugene-jurisdiction parcels (planning_jurisdiction_code='EUG') → Eugene PDD portal at eugene-or.gov (separate from state Accela; structured scrape IN FLIGHT, deep link surfaced); Springfield, smaller cities, and unincorporated Lane → Oregon state ePermitting Accela at aca-oregon.accela.com/oregon via fetch_county_permits with county_fips='039'. The function reads planning_jurisdiction_code from parcels_lane L1 and branches automatically. Use when the user asks about permits, building activity, development status on any Lane taxlot. Returns: routed_via='eugene_pdd' or 'oregon_state_accela', jurisdiction label, status (deep_link_only until scrape lands), permit_count, permits list, deep_links, in_flight notes. FORMATTING: TERRA aesthetic. 🚧 emoji header. Branch on routed_via — Eugene parcels frame as 'Eugene PDD has its own portal' with the PDD link; everything else frames as 'one tap from the live state portal' with the Accela link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Lane taxlot ID (13-char maptaxlot)."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_jackson_assessment": {
        "name": "fetch_jackson_assessment",
        "description": "TERRA: Fetch the Jackson County assessment + ownership + zoning snapshot for a taxlot (Medford / Ashland / Central Point / Eagle Point / Jacksonville / Phoenix / Talent / Shady Cove / unincorporated Jackson). Use when the user asks about a parcel in Southern Oregon — anywhere in the Rogue Valley or Jackson proper. JACKSON IS THE CLEANEST DATA ECOSYSTEM TERRA HAS TOUCHED — bulk taxlot service ships native FEEOWNER, SITEADD (situs), full mailing address, ACCOUNT, real-market values (LANDVALUE/IMPVALUE), assessed values (ASSESSLAND/ASSESSIMP), acreage, prop_class, year built, tax_code, commercial sqft, building code, lot dimensions. Zoning + comprehensive-plan designation come from a separate ZoningDistricts FeatureServer spatially joined at lookup time. ALL native — NO scraper, NO viewstate gating, NO upstream HTTP call. IN FLIGHT — flag honestly: multi-year tax payment history (PDO portal deep-link parameterization not yet captured; broker clicks through landing page). FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 🏠 SITUS / 🧾 ACCOUNT / 🏘️ ZONING / 💰 VALUATION / 🏗️ BUILDING / 🌍 ACREAGE / 🔗 DEEP LINKS). Highlight rmv_total in 💰 VALUATION. End with the property_data_online deep link as the broker hand-off.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Jackson taxlot ID (MAPLOT format, e.g. '372W25DA100')."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Currently no-op — Jackson L2 is a SQL pass-through over L1 inline + zoning spatial join; freshness comes from the daily L1 delta refresh."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_jackson_records": {
        "name": "fetch_jackson_records",
        "description": "TERRA: Fetch the Jackson County Clerk recorded-document snapshot for a taxlot. Returns the current fee-owner of record (Jackson's bulk taxlot service ships current ownership inline) plus deep links to the Digital Research Room (OnBase Public Access at apps.jacksoncountyor.gov) and the County Clerk recording info page. IN FLIGHT — Digital Research Room is OnBase Public Access with viewstate-gated document retrieval, same blocker as Marion MCASR / Washington washcotax / Lane Clerk. Full multi-instrument chain of title arrives once we wire OnBase viewstate capture or paid tier. FORMATTING: TERRA aesthetic. 📜 emoji header. Surface current owner-of-record prominently. Frame the IN FLIGHT honestly — 'full chain of title is one tap from the Digital Research Room' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Jackson taxlot ID (MAPLOT format)."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_jackson_permits": {
        "name": "fetch_jackson_permits",
        "description": "TERRA: Fetch building + land-use permits for a Jackson County parcel via the public GIS Permit FeatureServer. JACKSON IS SPECIAL — unlike Crook / Marion / Lane (where Accela is viewstate-blocked and we return deep-link-only), Jackson publishes 243k building permits + 40k land-use permits as a point FeatureServer at spatial.jacksoncountyor.gov DevServ/Permit with PRE-BUILT ACA-Oregon Accela CapDetail deep links on every record. fetch_jackson_permits runs a live spatial query (parcel envelope intersects permit point) and returns REAL STRUCTURED PERMITS. Use when the user asks about permits, building activity, development status on any Jackson taxlot. Returns permit_count, bldg_count, landuse_count, jurisdictions list, permits list (each with PERMITID, PERMITDESC, ESTCOST, SUBMITDT_iso, APPROVEDT_iso, APPLICANT, FULLADDR, LOCDESC, PERMITTYPE, PERMITSTAT, JURISDICTION, LINK to ACA CapDetail, STATUSCAT, _layer={'Building','Land Use'}). 24h cached. FORMATTING: TERRA aesthetic. 🚧 emoji header. Group by jurisdiction or status. Call out anything filed in the last 12 months as 'active development' vs older permits as 'history.' Surface the LINK on each permit as the broker hand-off to Accela detail. This is the cleanest permit data TERRA has — flex it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Jackson taxlot ID (MAPLOT format)."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Bypass 24h cache and re-pull from the live Jackson GIS Permit FeatureServer."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_wash_assessment": {
        "name": "fetch_wash_assessment",
        "description": "TERRA: Fetch the Washington County assessment + property snapshot for a taxlot. Use when the user asks about a parcel in Beaverton / Hillsboro / Tigard / Forest Grove / unincorporated Washington — anywhere west-of-Portland-metro. Returns acreage, map_number, taxlot_short, parcel centroid, live zoning code from the Intermap Land Use layer, plus verified deep links to the A&T portal (washcotax) and the official Washington County A&T page. IN FLIGHT — flag honestly: owner, situs, valuation snapshot, multi-year certified values, sales history, deed instruments, tax payment history all live behind washcotax.co.washington.or.us, which is DotNetNuke + ASP.NET WebForms with viewstate-gated TLNO → R-number resolution. Same fight as Crook's Accela scrape and Multnomah's MultcoPropTax — captcha-equivalent. Until the viewstate scrape or paid-tier lands, surface what we have (size + zoning + deep links) and frame the rest as 'coming soon — broker can click through.' FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (🌍 SIZE / 🏘️ ZONING / 📍 LOCATION / 🔗 DEEP LINKS). List IN FLIGHT items at end as 'coming soon' — never apologize. Always end with the at_search_by_taxlot deep link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Washington taxlot ID (TLNO format, 12 chars, e.g. '1N3150002100')."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Bypass 24h cache and re-pull from parcels_washington + live zoning query."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_wash_records": {
        "name": "fetch_wash_records",
        "description": "TERRA: Fetch Washington County recorded documents (deeds, mortgages, liens) for a taxlot. CURRENTLY IN FLIGHT — Washington County Clerk recordings live on a separate surface that requires paid-tier subscription, alternative source, or viewstate-captured washcotax search. Unlike Multnomah, Washington's bulk taxlot service does NOT ship deed/sale data inline, so there's no Layer-1 fallback — this fetcher returns IN FLIGHT plus the A&T search deep link and the County Clerk homepage. FORMATTING: TERRA aesthetic. 📜 emoji header. Frame the IN FLIGHT honestly — 'full chain of title coming once we wire the paid tier' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string"},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_washington_permits": {
        "name": "fetch_washington_permits",
        "description": "TERRA: Fetch building permits for a Washington County parcel via Washington's OWN Accela ACA instance at permits.washingtoncountyor.gov. NOT a fetch_county_permits wrapper — Washington runs a county-direct Accela, distinct from the state oregon.gov Accela used by Crook/Jefferson/Lane. Same deep-link-only IN FLIGHT pattern: server-side scrape gated on viewstate-capture or Playwright. Use when the user asks about permits, building activity, or development status on a Beaverton / Hillsboro / Tigard / unincorporated Washington taxlot. JURISDICTION NOTE — Beaverton, Hillsboro, and Tigard run separate city permit systems; v1 only covers the county Accela. Per-city portal routing is IN FLIGHT. FORMATTING: TERRA aesthetic. 🚧 emoji header. Frame the deep link as 'one tap from the live county portal.' Surface the jurisdiction note so brokers know city-level data may live elsewhere.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Washington taxlot ID (TLNO format)."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_clack_assessment": {
        "name": "fetch_clack_assessment",
        "description": "TERRA: Fetch the Clackamas County assessment + property snapshot for a taxlot. Use when the user asks about a parcel in Oregon City / Lake Oswego / West Linn / Milwaukie / Happy Valley / Wilsonville / unincorporated Clackamas — anywhere in Portland metro south or the Cascade foothills west of Mt Hood. Returns acreage (computed geodesically from geom since the source shape_area is Web Mercator m² and latitude-distorted), parcel_number (8-char Clackamas account #), tax_code area, situs_address / situs_city / situs_zip, parcel centroid, plus verified deep links to the ascendweb A&T portal (account-number search pre-fill) and homepage. IN FLIGHT — flag honestly: owner, valuation snapshot, multi-year certified values, sales history, deed instruments, zoning code/label, and tax payment history all live behind ascendweb.clackamas.us, which is ASP.NET WebForms with __VIEWSTATE-gated PARCEL_NUMBER → property-detail resolution. Same fight as washcotax (Washington), MultcoPropTax (Multnomah), and Crook's Accela scrape — viewstate-equivalent. Until the viewstate scrape or paid-tier lands, surface what we have (size + address + tax code + deep links) and frame the rest as 'coming soon — broker can click through.' FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (🌍 SIZE / 🏠 ADDRESS / 🏷️ TAX CODE / 📍 LOCATION / 🔗 DEEP LINKS). List IN FLIGHT items at end as 'coming soon' — never apologize. Always end with the at_account_search deep link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Clackamas taxlot ID (TLNO, up to 20 chars)."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Bypass 24h cache and re-pull from parcels_clackamas."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_clack_records": {
        "name": "fetch_clack_records",
        "description": "TERRA: Fetch Clackamas County recorded documents (deeds, mortgages, liens) for a taxlot. CURRENTLY IN FLIGHT — Clackamas County Clerk Recording Division has no public search portal located (recording.clackamas.us 404s; the division page lists only e-Recording submitter contacts — Simplifile / CSC / ePN — no read surface). Clackamas's hosted Taxlots view explicitly strips owner/sale/deed info, so there's no Layer-1 fallback. This fetcher returns IN FLIGHT plus the ascendweb account-search deep link and the Recording Division homepage. FORMATTING: TERRA aesthetic. 📜 emoji header. Frame the IN FLIGHT honestly — 'full chain of title coming once we wire the paid tier or e-Recording vendor' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string"},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_clackamas_permits": {
        "name": "fetch_clackamas_permits",
        "description": "TERRA: Fetch building permits for a Clackamas County parcel via Clackamas's OWN Accela ACA tenant at aca-prod.accela.com/clackamas. NOT a fetch_county_permits wrapper — Clackamas runs its own county-direct Accela, distinct from BOTH the state oregon.gov Accela (Crook/Marion/Jefferson/Lane) AND Washington's permits.washingtoncountyor.gov instance. Third distinct Accela tenant in TERRA. Same deep-link-only IN FLIGHT pattern: server-side scrape gated on viewstate-capture or Playwright. Also surfaces the Development Direct (Avolve cloud) link for new permit submissions, separate from the search surface. Use when the user asks about permits, building activity, or development status on an Oregon City / Lake Oswego / West Linn / Happy Valley / Milwaukie / Wilsonville / unincorporated Clackamas taxlot. JURISDICTION NOTE — the per-city portals (Oregon City / Lake Oswego / West Linn / Happy Valley / Milwaukie / Wilsonville) may host city-only permits separately; v1 only covers the county Accela. Per-city portal routing is IN FLIGHT. FORMATTING: TERRA aesthetic. 🚧 emoji header. Frame the Accela deep link as 'one tap from the live county portal.' Surface the jurisdiction note + Development Direct link so brokers know where to look for newer applications.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Full Clackamas taxlot ID (TLNO format)."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_linn_assessment": {
        "name": "fetch_linn_assessment",
        "description": "TERRA: Linn County (FIPS 41-043) assessment + property snapshot for a taxlot. Tier 2 Major county — mid-Willamette industrial corridor between Salem and Eugene (Albany / Lebanon / Sweet Home / Brownsville / Harrisburg / Halsey / Tangent / Scio / Mill City / Lyons). Built on parcels_linn Layer 1 (ORMAP cartographic polygons — Taxlot/MapTaxlot/ORTaxlot/MapNumber + three acreage measures) joined with Linn pub_sales MapServer (canonical sales surface — every row is a recorded deed with seller/buyer/instrument/sale_price/sale_date/site_addr/site_city/account/prop_class/sqft/yearblt/beds/baths). The latest pub_sales row gives a real owner + situs + last-sale snapshot. Use when the user asks about ownership / property snapshot / valuation / size / classification on a Linn County taxlot. Returns: {found, taxlot, map_taxlot, or_taxlot, map_number, account_number, snapshot{owner_latest, situs, last_sale, classification, improvements, area}, deep_links, in_flight[]}. IN FLIGHT: current-year RMV / AV / multi-year tax payment history — Linn County primary site is Cloudflare gated; per-account portal URL not yet captured. Snapshot is LAST-SALE row from pub_sales, not the current roll. FORMATTING: TERRA aesthetic. Vault-opening reveal. Honest IN FLIGHT.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Linn taxlot — accepts the 5-char suffix, MapTaxlot, or ORTaxlot."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_linn_records": {
        "name": "fetch_linn_records",
        "description": "TERRA: Linn County recorded-documents / deed chain / sales history for a taxlot. Built on Linn pub_sales MapServer — every row IS a deed Recording with Recording#/Instrument(WD/QC)/grantor/grantee/sale_price/sale_date/site_addr/site_city/financing/remark. Returns the FULL chain by PIN most-recent-first. Use when the user asks about deed chain / sales history / chain of title on a Linn taxlot. Returns: {found, taxlot, owner_name, snapshot, documents[], document_count, deep_links, in_flight[]}. Cached 24h. IN FLIGHT: Linn Clerk recordings direct search portal — county site Cloudflare-gated; portal URL not yet captured. FORMATTING: TERRA aesthetic. Recordings reverse-chronologically. Surface Recording# (e.g. DN 2022-4183) for Clerk cross-reference.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Linn taxlot — accepts the 5-char suffix, MapTaxlot, or ORTaxlot."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_linn_permits": {
        "name": "fetch_linn_permits",
        "description": "TERRA: Fetch building permits for a Linn County parcel. Routes by site_city pulled from the latest pub_sales row: (a) ALBANY -> permits.albanyoregon.gov + albanyor.buildingeye.com (Albany runs its OWN city portal — verified during s5 recon, NOT state Accela); (b) Lebanon / Sweet Home / Brownsville / Mill City / Lyons / Scio / Harrisburg / Halsey / Tangent / smaller cities / unincorporated Linn -> Oregon state Accela via fetch_county_permits(043); (c) Unknown jurisdiction -> dual-surface deep links. Returns: routed_via, jurisdiction, site_address, site_city, status, permits[], deep_links, in_flight notes. JURISDICTION NOTE — Albany is the ONLY incorporated Linn city verified during recon as running its own portal. FORMATTING: TERRA aesthetic. emoji header. Branch on routed_via.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Linn taxlot — accepts the 5-char suffix, MapTaxlot, or ORTaxlot."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_benton_assessment": {
        "name": "fetch_benton_assessment",
        "description": "TERRA: Fetch the Benton County assessment + property snapshot for a taxlot. Use when the user asks about a parcel in Corvallis / Philomath / Adair Village / Monroe / unincorporated Benton — anywhere in the Willamette Valley west of the river around Oregon State University. Returns aggregated owner_name (Benton's L1 is owner-exploded; STRING_AGG returns ALL co-trustees / co-owners), situs address, mailing address, account number, MapTaxlot + ORTaxlot, tax_code_area, map_number, computed acres (geodesic — Benton ships no native acres field), and verified deep links to the assessment portal. IN FLIGHT — per-parcel deep link (bcaps WordPress form is POST-only); RMV/AV/sales (behind assessment portal); zoning (separate Benton ZoningService FeatureServer). FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 🏠 SITUS / 📬 MAILING / 🌍 SIZE / 📍 LOCATION / 🔗 DEEP LINKS).",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Benton ORTaxlot (29-char canonical OR format, e.g. '0211.00S05.00W3400--000000100')."},
                "force_refresh": {"type": "boolean", "default": False}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_benton_records": {
        "name": "fetch_benton_records",
        "description": "TERRA: Fetch Benton County recorded documents for a taxlot. IN FLIGHT — Benton's recording portal at records.co.benton.or.us/Recording/ is session-token gated (no public GET deep-link). Returns current owner_name (aggregated via STRING_AGG over parcels_benton) + verified deep links to the recordings portal and Records & Elections landing. Open paths: session capture + scrape / paid Tyler integration / per-doc Clerk export. FORMATTING: TERRA aesthetic. 📜 emoji header. Frame the IN FLIGHT honestly. End with recordings_portal link.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_benton_permits": {
        "name": "fetch_benton_permits",
        "description": "TERRA: Fetch building permits for a Benton County parcel via Oregon's state ePermitting portal (Accela ACA). One-line wrapper around fetch_county_permits('003') — Benton participates in the SAME state Accela as Crook/Marion/Lane, confirmed via cd.bentoncountyor.gov/electronic-permitting linking to aca-oregon.accela.com/oregon. Brief reuse hypothesis VALIDATED — clean fetch_county_permits wrapper, not a county-direct Accela. IN FLIGHT — same deep-link-only pattern: scrape gated on viewstate-capture / Playwright. FORMATTING: TERRA aesthetic. 🚧 emoji header. Frame the deep link as 'one tap from the live state portal.'",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_yamhill_assessment": {
        "name": "fetch_yamhill_assessment",
        "description": "TERRA: Fetch the Yamhill County assessment + ownership snapshot for a taxlot (McMinnville / Newberg / Dundee / Carlton / Lafayette / Dayton / Sheridan / Willamina / Yamhill / Amity / unincorporated). Use when the user asks about a parcel in Willamette Valley wine country — Yamhill is THE Oregon vineyard county (Dundee Hills AVA + Eola-Amity Hills AVA + Yamhill-Carlton AVA + McMinnville AVA + Van Duzer Corridor AVA). Yamhill's Landowners feed is POLK-RICH on owner data: source ships joined parcel + assessor account attrs in ONE row, with OWNERLINE1 + OWNERLINE2 + OWNERLINE3 inline so trusts / family-LLC vineyard parcels surface all owners without an explosion table or scraper. Returns up-to-three owner names, full mailing block, situs (street + city + zip), PLSS-derived section_id (township-range-section + quarter-quarter), most-recent recording snapshot (instrument id / type / year / month — source ships no day, no sale price), acreage (polygon native + foot conversion), and account number. NO scraper, NO viewstate gating, NO HTTP per call — pure SQL pass-through over parcels_yamhill. IN FLIGHT — flag honestly, do not invent: NO valuation in source (Yamhill's feed exposes neither assessed nor RMV — broker pulls valuation via the account number at yamhillcounty.gov/assessor); property class PRPCLASS and description PRPCLSDSC are SCHEMA-PRESENT but empty for 100% of rows (vineyard / EFU / AF detection therefore surfaces via owner_line1 wine-LLC keyword scan + situs_city in AMITY / DUNDEE / DAYTON rather than via class fields); per-parcel REFLink schema-present but empty for 100% of rows; building details (year built / sqft / bed / bath) not exposed; Yamhill zoning service not yet located/ingested (Jackson-pattern follow-up); AGENTNAME field present but never populated. FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER — surface all_owners not just primary when owner_count > 1 / 🏠 SITUS / 🧾 ACCOUNT / 🌍 ACREAGE / 📜 LATEST RECORDING / 🔗 DEEP LINKS). When owner_line1 contains WINE / WINERY / VINEYARD / WINES / CELLARS, ADD A 🍇 VINEYARD header surfacing the wine-business identity before 💰 VALUATION (which is IN FLIGHT). End with the assessor_home + maps_portal deep links as broker hand-off.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Yamhill ORTaxlot (29-char canonical OR format, e.g. '3604.00S04.00W21BC--000000800' for the Yamhill County Courthouse) or MapTaxlot."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Currently no-op — Yamhill L2 is a SQL pass-through over L1 inline; freshness comes from the daily L1 delta refresh."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_yamhill_records": {
        "name": "fetch_yamhill_records",
        "description": "TERRA: Fetch the Yamhill County deed / recorded-instrument snapshot for a taxlot. Use when the user asks about deeds, recent transfer, recorded documents, or 'what was the last instrument on this Yamhill parcel'. Returns the MOST-RECENT instrument-of-record (instrument_id, type, year, month — source ships no day, synthesizes an ISO date as YYYY-MM-01; source ships NO recorded sale price), the current owners of record (up to three lines per source — multi-trustee / family-LLC vineyard parcels surface all owners), and deep links to the Yamhill County Clerk + assessor + ArcGIS maps portal. IN FLIGHT — flag honestly: multi-instrument deed chain (only the latest is in L1; full chain via the County Clerk portal — per-account deep link not yet captured, clerk_home is the surface); recorded sale price (not exposed by the public feed — sale chain lives behind the per-account assessor portal); instrument type (often blank in INSTTYPE source column, surface what's there and leave type null when missing). FORMATTING: TERRA aesthetic. 📜 emoji header. Lead with latest_instrument (instrument_id / type / year-month), then owners_of_record (surface all three owner lines when present), then the clerk_home deep link. Frame the IN FLIGHT as 'full chain of title is one tap from the County Clerk' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_yamhill_permits": {
        "name": "fetch_yamhill_permits",
        "description": "TERRA: Fetch building permits for a Yamhill County parcel via Oregon's state ePermitting portal (Accela ACA). One-line wrapper around fetch_county_permits('071') — Yamhill is Tier 2 Major. Per the Tier-2-rides-state-Accela pattern Clackamas confirmed, Yamhill jurisdictions — McMinnville, Newberg, Dundee, Carlton, Lafayette, Dayton, Sheridan, Willamina, Yamhill (city), Amity, and unincorporated — default-route through aca-oregon.accela.com/oregon. IN FLIGHT — McMinnville and Newberg are the two largest jurisdictions and MAY run their own city portals (recon hit 403 / 404 — per-city deep links not yet captured); until those resolve, the state-Accela landing is the canonical surface for ALL Yamhill jurisdictions, with per-city routing flagged. Same deep-link-only pattern as every other Tier-2 state-Accela county. FORMATTING: TERRA aesthetic. 🚧 emoji header. Surface jurisdiction ('Yamhill County — Oregon state Accela; Tier 2 reuse pattern. McMinnville + Newberg city portals IN FLIGHT'). Frame the deep link as 'one tap from the live state portal.'",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_polk_assessment": {
        "name": "fetch_polk_assessment",
        "description": "TERRA: Fetch the Polk County assessment + ownership snapshot for a taxlot (Dallas / Independence / Monmouth / Falls City / Willamina / West Salem-adjacent / unincorporated Polk). Use when the user asks about a parcel in west Willamette Valley around Salem's west bank, the Polk side of the Salem metro, or the Coast-Range-east hill country. Polk's public taxlot service is MARION-RICH: source ships joined parcel + AcctTable attrs in ONE row — owner_line1, agent_name, in_care_of, full mailing block (addr1/2/city/state/zip/country), situs (street + city + zip), account_id + prim_acc_num, last instrument (id / type / year / month — source ships no day, no recorded sale price), property class + description, dwelling flag, assessed values (land / improvement / total — source ships NO RMV-family valuation, only assessed), tax routing (SA / MA / NH / TaxCode / TaxCodeArea / Unit_ID), acreage (polygon native + account-of-record + sqft). NO scraper, NO viewstate gating, NO HTTP per call — pure SQL pass-through over parcels_polk. IN FLIGHT — flag honestly, do not invent: real-market valuation (Polk MapServer exposes only assessed); building details (year built / sqft / bed / bath — not exposed); zoning (separate Polk MapServer not yet ingested); Polk's www.polkcountyor.gov direct portal subpages return 404 — only maps.co.polk.or.us/pcmaps + the PSO Property Search Online portal are reachable for human follow-up. FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 🏠 SITUS / 🧾 ACCOUNT / 💰 VALUATION / 🌍 ACREAGE / 🔗 DEEP LINKS). Surface assessed_total prominently in 💰 VALUATION; explicitly note RMV is not in source. End with the pso_search + polk_maps deep links as broker hand-off.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Polk ORTaxlot (29-char canonical OR format, e.g. '2707.00S05.00W33BC--000006300' for the Polk County Courthouse) or MapTaxlot."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Currently no-op — Polk L2 is a SQL pass-through over L1 inline; freshness comes from the daily L1 delta refresh."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_polk_records": {
        "name": "fetch_polk_records",
        "description": "TERRA: Fetch the Polk County deed / recorded-instrument snapshot for a taxlot. Use when the user asks about deeds, recent transfer, recorded documents, or 'what was the last instrument on this Polk parcel'. Returns the MOST-RECENT instrument-of-record (instrument_id, type, year, month — source ships no day, synthesizes an ISO date as YYYY-MM-01; source ships NO recorded sale price), the current owner of record (primary + agent + in_care_of + full mailing block), plus deep links to the Polk Clerk Digital Research Room and the maps portal. IN FLIGHT — flag honestly: multi-instrument deed chain (only the latest instrument is in our L1 snapshot; full chain lives in the Polk Clerk Digital Research Room 1984-present index, one tap from the deep link); recorded sale price (Polk MapServer exposes neither the sale price nor the day-of-month — relationshipId 0 to PCMAPS.DBO.V_REAL_SALES carries the price chain, queryable on demand but queued as follow-up). FORMATTING: TERRA aesthetic. 📜 emoji header. Lead with latest_instrument (instrument_id / type / year-month), then owner_of_record, then the digital_research_room deep link. Frame the IN FLIGHT as 'full chain of title is one tap from the Clerk's Digital Research Room' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_douglas_assessment": {
        "name": "fetch_douglas_assessment",
        "description": "TERRA: Fetch the Douglas County assessment + ownership + zoning + timber snapshot for a taxlot (Roseburg / Sutherlin / Winston / Myrtle Creek / Canyonville / Reedsport / Drain / Yoncalla / Oakland / Glendale / Riddle / unincorporated). Use when the user asks about a Douglas County parcel — Southern Oregon I-5 corridor between Eugene and Medford, with major timber + agricultural acreage. Douglas's public Parcels FeatureServer is MARION-RICH plus richer than Polk: source ships joined parcel + account attrs in ONE row — owner_name (NAME, 350 char), full mailing block (addr1/2/3 + csz), situs (address + csz), account family (taxid + prop_id + alt_acctnum + owner_id), property class (PROPCLASS — 9xx prefix = forest / timber / O&C grant land), full valuation breakdown (assessed + RMV land + RMV imp + RMV total — RICHER than Polk which only ships assessed), acreage (account-of-record + material + total/GIS), latest instrument (inst_no + sale_date), legal description (unbounded). NO scraper, NO viewstate gating, NO HTTP per call — pure SQL pass-through over parcels_douglas + spatial join against douglas_zoning (DC_ZONE — F1/F2/F3/FF/FG = forest variants, AW = agricultural watershed / EFU equivalent, R1-R3/RR residential, C1-C3 commercial, M1-M3 industrial). TIMBER MP SIGNAL: returns a structured `timber` block flagging forest-class (PROPCLASS 9xx) + forest-zone (DC_ZONE F-prefix) parcels — TIMBER-LAND MARKETING PARTNER differentiator per KB_75 / TERRA-CLOSER-01. IN FLIGHT — flag honestly: multi-year tax payment history (Orion Taxlot Information deep-links are session-gated); full chain of title (Clerk recordings session-gated); building details (year built / sqft / bed / bath — not on the Parcels FeatureServer). FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 🏠 SITUS / 🧾 ACCOUNT / 🏘️ ZONING / 🌲 TIMBER if timber_mp_signal=true / 💰 VALUATION / 🌍 ACREAGE / 📜 LATEST INSTRUMENT / 🔗 DEEP LINKS). Surface RMV total + assessed prominently in 💰 VALUATION (call out the RMV/assessed split — Douglas ships both, unlike Polk). When `timber.timber_mp_signal=true`, ADD a 🌲 TIMBER section that names the forest PROPCLASS and/or forest DC_ZONE — this is the differentiating fact for the timber MP angle. End with assessment_search + gis_viewer deep links as broker hand-off.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Douglas TAXID (canonical 20-char Douglas taxlot, e.g. '19080000100')."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Currently no-op — Douglas L2 is a SQL pass-through over L1 inline; freshness comes from the daily L1 delta refresh."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_douglas_records": {
        "name": "fetch_douglas_records",
        "description": "TERRA: Fetch the Douglas County deed / recorded-instrument snapshot for a taxlot. Use when the user asks about deeds, recent transfer, recorded documents, or 'what was the last instrument on this Douglas parcel'. Returns the LATEST instrument-of-record (inst_no + sale_date — sourced from the Parcels FeatureServer, not the Clerk portal) plus the current owner of record (primary + mailing block) and deep links to the Clerks-Office landing + Geocortex GIS viewer (which deep-links to Orion Taxlot Information for full document history). IN FLIGHT — flag honestly: full multi-instrument deed chain (Clerk recordings portal is session-gated — only the latest instrument is in our L1 snapshot; full chain lives behind the Clerks-Office landing); recorded sale price history (not on the Parcels FeatureServer — Orion deep follow-up required). FORMATTING: TERRA aesthetic. 📜 emoji header. Lead with latest_instrument (inst_no + sale_date), then owner_of_record, then the clerks_office deep link. Frame the IN FLIGHT as 'full chain of title is one tap from the Douglas County Clerks Office landing' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_douglas_permits": {
        "name": "fetch_douglas_permits",
        "description": "TERRA: Fetch building permits for a Douglas County parcel via Oregon's state ePermitting portal (Accela ACA). One-line wrapper around fetch_county_permits('019') — Douglas is Tier 2 Major (Roseburg / Sutherlin / Winston / Myrtle Creek / Canyonville / Reedsport / Drain / Yoncalla / Oakland / Glendale / Riddle / unincorporated). Per the Tier 2 reuse pattern (Polk / Benton / Marion / Lane / Linn): all Douglas jurisdictions route through aca-oregon.accela.com/oregon — no Douglas-direct Accela tenant surfaces from civicplus or city-of-Roseburg pages. IN FLIGHT — same deep-link-only pattern as the Tier 2 cohort: scrape gated on viewstate capture / Playwright. FORMATTING: TERRA aesthetic. 🚧 emoji header. Surface jurisdiction ('Douglas County — Oregon state Accela; Tier 2 reuse pattern'). Frame the deep link as 'one tap from the live state portal.'",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_josephine_assessment": {
        "name": "fetch_josephine_assessment",
        "description": "TERRA: Fetch the Josephine County assessment + ownership + zoning + recent-sale snapshot for a taxlot (Grants Pass / Cave Junction / Merlin / Selma / Williams / Wolf Creek / Kerby / O'Brien / Wilderville / Sunny Valley / Applegate / unincorporated). Use when the user asks about a Josephine parcel — Southern Oregon Rogue Valley west half, paired with Jackson for the full Southern Oregon broker audience. Josephine ships the RICHEST L1 inline surface in TERRA: source ships ALL of — owner_name (NAME), full mailing block (ADDR1/2/3 + City/State/ZIP/CSZ), situs parsed by component (SITUS + SITUS_CITY + SITUS_ST + SITUS_ZIP + ST_NO + SITUS_PREF + ST_NAME + SITUS_SUFF), account (R-number ACCOUNT + ACCTSTATUS + map identifiers), valuation FULLY DECOMPOSED (RMV total + LAND_MKT + LAND_APPR + IMP_VALUE + ASSD_VALUE + APPR_VALUE — richer than Polk which ships only assessed, richer than Douglas which ships assessed + RMV without the appraised breakdown), 3 acreage sources (ACREAGE / LEGAL_ACRE / GIS_Acres), building details (YR_BLT + SQ_FT + LIVING_AREA + BEDRMS + BLDG_CLASS + MH_MAKE), tax routing (CODE / MAINT / NBHD), INLINE ZONING (Zone field — no separate zoning service needed, unlike Jackson), AND the most-recent recorded sale fully decomposed (SALE_DATE + SALE_PRICE + DEED_TYPE + INST_NO + SALE_TYPE — richer than Jackson which surfaces owner only, richer than Polk which ships no sale price). NO scraper, NO viewstate gating, NO HTTP per call — pure SQL pass-through over parcels_josephine. IN FLIGHT — flag honestly, do not invent: JCPA per-account deep-link parameterization (lookup is ASP.NET viewstate-gated; the broker clicks through and pastes ACCOUNT to land on parcel detail); multi-year tax payment history (same viewstate-gating as Marion / Jackson PDO); full multi-instrument chain of title (Clerk's Tessera Public Records index, session-gated). FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 🏠 SITUS / 🧾 ACCOUNT / 🏘️ ZONING / 💰 VALUATION / 🌍 ACREAGE / 🏗️ BUILDING / 📜 RECENT SALE / 🔗 DEEP LINKS). Surface RMV total + assessed total prominently — both are inline, call out the RMV vs assessed split. Surface 📜 RECENT SALE prominently when recent_sale.date is present — Josephine ships the latest deed inline (rare and powerful). End with property_data_lookup + clerk_recording deep links as broker hand-off.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Josephine MapNum (14-char canonical, e.g. '36050800003500' for Josephine County Courthouse area) or MNX (16-char extended) or ACCOUNT (R-number, e.g. 'R300740')."},
                "force_refresh": {"type": "boolean", "default": False, "description": "Currently no-op — Josephine L2 is a SQL pass-through over L1 inline; freshness comes from the daily L1 delta refresh."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_josephine_records": {
        "name": "fetch_josephine_records",
        "description": "TERRA: Fetch the Josephine County deed / recorded-instrument snapshot for a taxlot. Use when the user asks about deeds, the recent transfer, recorded documents, or 'what was the last instrument on this Josephine parcel.' Returns the MOST-RECENT recorded sale fully decomposed (SALE_DATE + SALE_PRICE + DEED_TYPE + INST_NO + SALE_TYPE — richer than Jackson which surfaces owner only, richer than Polk which ships no sale price), the current owner of record (primary + full mailing block), plus deep links to the Josephine Clerk Recording office and the Property Data Lookup (JCPA). IN FLIGHT — flag honestly: multi-instrument deed chain (only the most-recent is inline; full chain lives in the Clerk's Tessera Public Records index, session-gated — the broker uses the deep link to search by instrument number or grantor/grantee); recorded plats / partitions / easements separate from the conveyance chain (not in the parcels service). FORMATTING: TERRA aesthetic. 📜 emoji header. Lead with latest_recorded_sale (date / price / deed_type / instrument_no — Josephine is the FIRST TERRA county where the most-recent recorded sale comes with all five fields inline, flex it), then owner_of_record, then the clerk_recording deep link. Frame the IN FLIGHT as 'full multi-instrument chain is one tap from the Clerk's Tessera index' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_josephine_permits": {
        "name": "fetch_josephine_permits",
        "description": "TERRA: Fetch building permits for a Josephine County parcel — FIRST ENERGOV ROUTING IN TERRA. Routes by situs_city. Grants Pass parcels → EnerGov SelfService (selfservice.grantspassoregon.gov/energov_prod/selfservice) — city-direct, NOT state Accela. Grants Pass is the FIRST Oregon city in TERRA running EnerGov (Tyler Technologies) rather than Accela; this is a routing-pattern breakthrough that will repeat in other Oregon EnerGov cities at Tier 3/4. Everything else (Cave Junction + Merlin + Selma + Williams + Wolf Creek + O'Brien + Wilderville + Kerby + Sunny Valley + Applegate + unincorporated) → Oregon state ePermitting Accela via fetch_county_permits('033'). Per the Tier-2-rides-state-Accela pattern (Clackamas confirmed Tier-1-flagships-run-county-direct, Tier-2+ ride state for unincorporated). Returns shape parallel to every other county's permits fetcher: {found, taxlot, county_fips, jurisdiction, situs_city, situs_address, routed_via ('grants_pass_energov_selfservice' OR 'oregon_state_accela'), status, permit_count, permits, deep_links, in_flight[], fetched_at}. IN FLIGHT for Grants Pass: EnerGov SelfService per-parcel deep-link parameterization (search-query schema not yet captured — broker pastes situs or ACCOUNT into SelfService search). IN FLIGHT for state-Accela jurisdictions: same deep-link-only pattern as every other Tier-2 state-Accela county (viewstate-gated retrieval). FORMATTING: TERRA aesthetic. 🚧 emoji header. Lead with routed_via so the broker immediately knows whether they're going to EnerGov or Accela. Surface the appropriate deep link (energov_selfservice OR accela_search) as the one-tap broker hand-off. Frame the EnerGov routing as 'first EnerGov surface in TERRA — Grants Pass joins a small Oregon-EnerGov cohort.' Never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_tillamook_assessment": {
        "name": "fetch_tillamook_assessment",
        "description": "TERRA: Fetch the Tillamook County assessment + ownership + VALUATION snapshot for a taxlot (Tillamook / Garibaldi / Bay City / Rockaway Beach / Manzanita / Nehalem / Wheeler / Pacific City / unincorporated — Oregon's north coast: beach tourism + STR layered over the dairy belt). Use when the user asks about a Tillamook parcel. Source is a hosted ArcGIS Online Taxlot Owners feed, pure SQL pass-through over parcels_tillamook L1 inline — NO scraper, NO viewstate gating, NO HTTP per call. RICHEST ODF-family L2 in TERRA because VALUATION SHIPS INLINE AND LIVE: ASSESSVAL (assessed total, 90% populated) + LANDVALUE + IMPVALUE — surface assessed_total as the headline number (contrast Yamhill / Jefferson which ship no valuation). Also ships: owner stack (OWNERLINE1/2/3, 95%), full mailing block, situs (SITEADDNAM + SITEADDCTY, 63%), account + tax status (PRIMACCNUM / ACCTSTATUS / TAXSTATUS), property class (PRPCLASS / PRPCLSDSC), PLSS-derived section, acreage (TaxlotAcre + MapAcres + geodesic), most-recent recording snapshot (INSTID / INSTTYPE / INSTYEAR / INSTMONTH). IN FLIGHT — flag honestly, never invent: RMV (feed ships ASSESSED + land + improvement, not a distinct real-market figure); building details (year built / sqft / bed / bath not in source); zoning (no Tillamook zoning service ingested yet — Jackson-pattern follow-up; PRPCLSDSC carries use class); per-parcel assessor deep link (county site WAF-blocks datacenter IPs). FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 🏠 SITUS / 🧾 ACCOUNT / 💰 VALUATION / 🌍 ACREAGE / 📜 RECORDING / 🔗 DEEP LINKS). Surface 💰 VALUATION prominently — Tillamook is the coastal county where assessed value is LIVE. End with the taxlot_rest deep link as broker hand-off. Never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Tillamook ORTaxlot (29-char, e.g. '2901.00S09.00W30BC--000004300') or MapTaxlot or PRIMACCNUM."},
                "force_refresh": {"type": "boolean", "default": False, "description": "No-op — Tillamook L2 is a SQL pass-through over L1 inline; freshness comes from the L1 delta refresh."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_tillamook_records": {
        "name": "fetch_tillamook_records",
        "description": "TERRA: Fetch the Tillamook County recorded-document snapshot for a taxlot. Use when the user asks about deeds, the recent transfer, or current ownership of a Tillamook parcel. Returns the MOST-RECENT instrument-of-record (INSTID / INSTTYPE / INSTYEAR / INSTMONTH → synthesized ISO date; source ships no day, no sale price) and the current owners of record (up to 3 lines + mailing) from parcels_tillamook L1 inline, plus deep links. IN FLIGHT — flag honestly: multi-instrument deed chain (only the latest is inline; County Clerk recording surface is WAF-blocked from the droplet — broker navigates from the county home to County Clerk Records); recorded sale price (not in source). FORMATTING: TERRA aesthetic. 📜 emoji header. Lead with latest_instrument, then owners_of_record, then the deep link. Frame the IN FLIGHT as 'full chain is one navigation from County Clerk Records' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_tillamook_permits": {
        "name": "fetch_tillamook_permits",
        "description": "TERRA: Fetch building permits for a Tillamook County parcel via Oregon's state ePermitting portal (Accela ACA). One-line wrapper around fetch_county_permits('057') — Tillamook is Tier 3 Standard. Per the established pattern (Tier 1 Flagships run county-direct Accela tenants, Tier 2/3/4 ride state Accela), all Tillamook jurisdictions — Tillamook (city), Garibaldi, Bay City, Rockaway Beach, Manzanita, Nehalem, Wheeler, Pacific City, and unincorporated — route through aca-oregon.accela.com/oregon. No dedicated Tillamook permits GIS FeatureServer exists, so the state-Accela deep link is the canonical surface. IN FLIGHT — same deep-link-only pattern as every other state-Accela county (scrape gated on viewstate capture / Playwright). FORMATTING: TERRA aesthetic. 🚧 emoji header. Surface jurisdiction ('Tillamook County — Oregon state Accela; Tier 3 reuse pattern') and the accela_search deep link as 'one tap from the live state portal.'",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_clatsop_assessment": {
        "name": "fetch_clatsop_assessment",
        "description": "TERRA: Fetch the Clatsop County assessment + ownership + situs snapshot for a taxlot (Astoria / Seaside / Cannon Beach / Gearhart / Warrenton / unincorporated — Oregon's NW-corner coast, tourism + STR market). Use when the user asks about a Clatsop parcel. Source is Clatsop County's OWN Taxlots FeatureServer (not the ODF overlay), pure SQL pass-through over parcels_clatsop L1 inline — NO scraper, NO viewstate gating, NO HTTP per call. Ships: owner (up to 3 lines OWNER_LINE/OWNER_LL_1/OWNER_LL_2 + IN_CARE_OF, 99.8% populated), full mailing block, situs (SITUS_ADDR + SITUS_CITY, 68%), account (ACCOUNT_ID, 99.9%), property class (PROPERTY_C + STAT_CLASS), YEAR_BUILT (67%), tax code, maintenance-area / neighborhood, septic status, and geodesic acreage. IN FLIGHT — flag honestly, never invent: VALUATION (no assessed/RMV in the public layer — token-gated Assessment_Tax folder; broker pulls from Public Property Search via account_id); ZONING (not on the taxlot record — separate Zoning_Layers MapServer with county + Astoria/Cannon Beach/Gearhart/Seaside/Warrenton layers, ingest queued Jackson-pattern); recorded sale / chain of title (no instrument fields — see fetch_clatsop_records); building detail beyond year built. FORMATTING: TERRA SCAN aesthetic. ✨ vault-opening reveal. Section headers with emoji (👤 OWNER / 🏠 SITUS / 🧾 ACCOUNT / 🏗️ BUILDING / 🌍 ACREAGE / 🔗 DEEP LINKS). Surface owner stack + situs + year_built prominently. For the coastal STR audience, surface the transient_room_tax deep link. End with public_property_search as the broker hand-off. Never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Clatsop ORTaxlot (29-char, e.g. '0408.00N09.00W08CD--000000400') or short Taxlot / TAXMAPKEY / TAXLOTKEY or numeric ACCOUNT_ID."},
                "force_refresh": {"type": "boolean", "default": False, "description": "No-op — Clatsop L2 is a SQL pass-through over L1 inline; freshness comes from the L1 delta refresh."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_clatsop_records": {
        "name": "fetch_clatsop_records",
        "description": "TERRA: Fetch the Clatsop County owner-of-record + recording snapshot for a taxlot. Use when the user asks about deeds, recorded documents, or current ownership of a Clatsop parcel. Returns the current owner(s) of record (up to 3 lines + in-care-of) and mailing block from parcels_clatsop L1 inline, plus deep links to Clatsop County Property Records and Public Records Request. IN FLIGHT — flag honestly: the FULL recorded-instrument chain of title (deed history, instrument numbers, recording dates, sale price) is NOT exposed by the public Clatsop Taxlots layer — no instrument fields in source. The County Property Records surface is the broker hand-off until a structured recording source is wired. FORMATTING: TERRA aesthetic. 📜 emoji header. Lead with owner_of_record, then surface the property_records deep link. Frame the IN FLIGHT as 'full recorded chain is one tap from County Property Records' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_clatsop_permits": {
        "name": "fetch_clatsop_permits",
        "description": "TERRA: Fetch building permits for a Clatsop County parcel via Oregon's state ePermitting portal (Accela ACA). One-line wrapper around fetch_county_permits('007') — Clatsop is Tier 3 Standard. Per the established pattern (Tier 1 Flagships run county-direct Accela tenants, Tier 2/3/4 ride state Accela), all Clatsop jurisdictions — Astoria, Seaside, Cannon Beach, Gearhart, Warrenton, and unincorporated Clatsop — route through aca-oregon.accela.com/oregon. No dedicated Clatsop permits GIS FeatureServer exists (the county Code_Compliance MapServer is code enforcement, not building permits), so the state-Accela deep link is the canonical surface. IN FLIGHT — same deep-link-only pattern as every other state-Accela county: server-side scrape gated on viewstate capture / Playwright. FORMATTING: TERRA aesthetic. 🚧 emoji header. Surface jurisdiction ('Clatsop County — Oregon state Accela; Tier 3 reuse pattern') and the accela_search deep link as 'one tap from the live state portal.'",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_lincoln_assessment": {
        "name": "fetch_lincoln_assessment",
        "description": "TERRA: Fetch the Lincoln County parcel + assessment snapshot for a taxlot (Newport / Lincoln City / Toledo / Waldport / Depoe Bay / Yachats / Siletz / unincorporated — Oregon's central coast: beach tourism + STR market). Use when the user asks about a Lincoln parcel. THIN / WASHINGTON-PATTERN ship: Lincoln County runs NO ArcGIS Server, so the full-county L1 (the hosted taxlot21 ORMAP cadastre) ships ONLY polygon attributes — taxlot identifiers / PLSS-derived section / acreage (TaxlotAcre) / map number / REFLink. SQL pass-through over parcels_lincoln, no HTTP per call. Owner / situs / valuation / instrument are NOT in the county-wide cadastre feed — they surface WHEN PRESENT (currently the ~8,373 Newport-UGB parcels after the queued enrichment; NULL elsewhere) and are otherwise flagged IN FLIGHT honestly, NEVER fabricated. IN FLIGHT (flag honestly): owner/mailing/situs county-wide (behind the county GeoMOOSE/WFS a_assessment.map; broker pulls from the Property Information Search page co.lincoln.or.us/1000); valuation (assessed/RMV); building details; recorded instrument. FORMATTING: TERRA SCAN aesthetic. ✨ reveal. Section headers with emoji (🗺️ PARCEL / 📐 SECTION / 🌍 ACREAGE / 👤 OWNER / 🔗 DEEP LINKS). Lead with the parcel identifiers + acreage (what IS live), then surface owner/situs IF present, then frame owner/valuation as 'one tap from Property Information Search.' For the coastal STR audience, surface the transient_lodging_tax deep link. Never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "taxlot": {"type": "string", "description": "Lincoln ORTaxlot (29-char, e.g. '2107.00S11.00W15DC--000015800') or MapTaxlot, or (post-enrichment) PRIMACCNUM."},
                "force_refresh": {"type": "boolean", "default": False, "description": "No-op — Lincoln L2 is a SQL pass-through over L1 inline."}
            },
            "required": ["taxlot"]
        }
    },
    "fetch_lincoln_records": {
        "name": "fetch_lincoln_records",
        "description": "TERRA: Fetch the Lincoln County recorded-document snapshot for a taxlot. Use when the user asks about deeds / recorded documents / ownership of a Lincoln parcel. THIN ship: the full-county cadastre feed carries NO recorded-instrument data, so the recording chain is IN FLIGHT — surfaced via the County Document Recording deep link (co.lincoln.or.us/177) and Property Information Search. Owner-of-record + latest instrument surface WHEN PRESENT (Newport-UGB subset after the queued enrichment). IN FLIGHT (flag honestly): recorded-instrument chain + sale price (not in cadastre feed); owner-of-record county-wide (behind GeoMOOSE/WFS). FORMATTING: TERRA aesthetic. 📜 emoji header. Surface document_recording + property_information_search deep links. Frame as 'full chain is one tap from County Document Recording.' Never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_lincoln_permits": {
        "name": "fetch_lincoln_permits",
        "description": "TERRA: Fetch building permits for a Lincoln County parcel via Oregon's state ePermitting portal (Accela ACA). One-line wrapper around fetch_county_permits('041') — Lincoln is Tier 3 Standard. Per the established pattern (Tier 1 Flagships run county-direct Accela tenants, Tier 2/3/4 ride state Accela), all Lincoln jurisdictions — Newport, Lincoln City, Toledo, Waldport, Depoe Bay, Yachats, Siletz, and unincorporated — route through aca-oregon.accela.com/oregon. No dedicated Lincoln permits GIS FeatureServer exists, so the state-Accela deep link is the canonical surface. IN FLIGHT — same deep-link-only pattern as every other state-Accela county. FORMATTING: TERRA aesthetic. 🚧 emoji header. Surface jurisdiction ('Lincoln County — Oregon state Accela; Tier 3 reuse pattern') and the accela_search deep link as 'one tap from the live state portal.'",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_polk_permits": {
        "name": "fetch_polk_permits",
        "description": "TERRA: Fetch building permits for a Polk County parcel via Oregon's state ePermitting portal (Accela ACA). One-line wrapper around fetch_county_permits('053') — Polk is Tier 2 Major. Per the emerging pattern Clackamas confirmed (Tier 1 Flagships run county-direct Accela tenants, Tier 2/3/4 ride state Accela), Polk jurisdictions — Dallas, Independence, Monmouth, Falls City, Willamina, and unincorporated Polk — all route through aca-oregon.accela.com/oregon. Brief reuse hypothesis VALIDATED — clean fetch_county_permits wrapper, not a county-direct Accela. IN FLIGHT — same deep-link-only pattern as Crook/Marion/Benton: scrape gated on viewstate capture / Playwright. FORMATTING: TERRA aesthetic. 🚧 emoji header. Surface jurisdiction ('Polk County — Oregon state Accela; Tier 2 reuse pattern'). Frame the deep link as 'one tap from the live state portal.'",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_jefferson_assessment": {
        "name": "fetch_jefferson_assessment",
        "description": "TERRA: Fetch the Jefferson County assessment + ownership + situs snapshot for a taxlot (Madras / Culver / Metolius / Crooked River Ranch / unincorporated — completes the Central Oregon trio with Deschutes + Crook). Use when the user asks about a Jefferson parcel. Source is the Deschutes-hosted COFSA Jefferson Taxlots aggregator (the ODF statewide overlay has /query DISABLED — Map-only capability), pure SQL pass-through over parcels_jefferson L1 inline — NO scraper, NO HTTP per call. Ships: owner (single OWNER line, 100% populated), situs (HOUSE/STREET/CITY/ZIP composed, ~69% populated), MAPTAXLOT, and GEOMETRY-DERIVED geodesic acreage. CRITICAL: COFSA ships ACRES 0% populated, so acreage is computed from the parcel polygon (ST_Area geodesic) — accurate to geometry but NOT the assessor's stated legal acreage; always label it 'geometry-derived'. IN FLIGHT — flag honestly, NEVER invent: VALUATION (no assessed/RMV in COFSA — broker pulls from jeffersoncountyor.gov/assessor); RECORDED INSTRUMENT / sale price (not in source — see fetch_jefferson_records); owner stack beyond primary, mailing, PLSS section, property class (columns exist Yamhill-family but COFSA leaves NULL — surface if backfilled); ZONING (no Jefferson zoning service located yet, Jackson-pattern ingest queued). FORMATTING: TERRA SCAN aesthetic. NO markdown tables (pipes render raw). Section headers with emoji (👤 OWNER / 🏠 SITUS / 🧾 ACCOUNT / 🌍 ACREAGE / 🔗 DEEP LINKS). Surface owner + situs + geodesic acreage prominently; label acreage honestly. End with the assessor deep link as the broker hand-off for live valuation. Never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_jefferson_records": {
        "name": "fetch_jefferson_records",
        "description": "TERRA: Fetch the Jefferson County owner-of-record snapshot for a taxlot. Use when the user asks about deeds, recorded documents, or current ownership of a Jefferson parcel. Returns the current owner of record from parcels_jefferson L1 inline (COFSA) plus deep links to the Jefferson County Clerk and Assessor surfaces. IN FLIGHT — flag honestly: the FULL recorded-instrument chain of title (deed history, instrument numbers, recording dates, sale price) is NOT exposed by the COFSA Jefferson Taxlots layer — no instrument fields in source. The Jefferson County Clerk surface is the broker hand-off until a structured recording source is wired. FORMATTING: TERRA aesthetic. 📜 emoji header. Lead with owner_of_record, then surface the clerk_records deep link. Frame the IN FLIGHT as 'full recorded chain is one tap from the County Clerk' — never apologize, never fabricate.",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "fetch_jefferson_permits": {
        "name": "fetch_jefferson_permits",
        "description": "TERRA: Fetch building permits for a Jefferson County parcel via Oregon's state ePermitting portal (Accela ACA). One-line wrapper around fetch_county_permits('031') — Jefferson is Tier 3. FIPS 031 was added to _OREGON_EPERMITTING_FIPS during the Crook ship, so all Jefferson jurisdictions — Madras, Culver, Metolius, and unincorporated Jefferson — route through aca-oregon.accela.com/oregon. IN FLIGHT — same deep-link-only pattern as every other state-Accela county: server-side scrape gated on viewstate capture / Playwright. FORMATTING: TERRA aesthetic. 🚧 emoji header. Surface jurisdiction ('Jefferson County — Oregon state Accela; Tier 3 reuse pattern') and the accela_search deep link as 'one tap from the live state portal.'",
        "input_schema": {
            "type": "object",
            "properties": {"taxlot": {"type": "string"}, "force_refresh": {"type": "boolean", "default": False}},
            "required": ["taxlot"]
        }
    },
    "calculate_lot_yield": {
        "name": "calculate_lot_yield",
        "description": "TERRA — SPECULATIVE residential lot-yield analysis. Verify with planning office before quoting. Computes a rough estimate of how many residential lots could theoretically be carved from a Deschutes County parcel and (optionally) the gross retail value if the broker provides a per-lot market estimate. CALL when a broker asks 'how many lots can I get on parcel X', 'what's parcel X worth as a development', 'what's the yield on this taxlot', or after a TERRA SCAN COMPLETE on a residential parcel ≥1.0 acre. INPUTS: parcel_id (taxlot string, required). Optional: deduction_override (0-1 fraction for roads/utilities/setbacks; default 0.25 = 25%), per_lot_value_override (USD per finished lot — broker's own market number; if omitted, the tool returns a yield-only result and prompts the broker for their estimate), target_zone_override (force a different zone for what-if rezone scenarios). BEHAVIOR: (a) if the parcel has no zoning on file, the tool live-queries the county GIS service and caches the result — adds ~500ms latency but completes the call. (b) Non-residential zones return an informational dict (not an error) noting yield-calc doesn't apply. (c) Zones outside the seed lookup return a structured error with guidance — NO fake numbers. (d) Every result carries a footer disclaimer: 'TERRA estimate — actual yield subject to site conditions, plat approval, and jurisdictional review. Verify with the city/county planning office before underwriting.' DOCTRINE: per DOCTRINE-TERRA-ANALYSIS-TOOL-01, this tool is speculative analysis only — never quote outputs as certified yield or appraised value. Per DOCTRINE-YIELD-HOOK-THEN-CLOSE-01, the v1 output is the broker hook; v2 'deep dive' refinement (setbacks, slope, wetlands) comes later. FORMATTING for Sophia's reply: render the result as a TERRA card with the broker-friendly numbers up front (lot_yield, gross_retail if available), then assumptions, then the footer. NEVER omit the footer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "parcel_id": {"type": "string", "description": "Deschutes County taxlot identifier (e.g. '151319AC00139')."},
                "deduction_override": {"type": "number", "description": "Optional. Fraction (0-1) deducted from gross acres for roads/utilities/setbacks. Default 0.25."},
                "per_lot_value_override": {"type": "number", "description": "Optional. Broker-supplied market value per finished lot in USD. Omit to get a yield-only card; the tool will prompt for the broker's market estimate."},
                "target_zone_override": {"type": "string", "description": "Optional. Force-apply a different zone code (e.g. for a rezone what-if). Must match the same jurisdiction as the parcel."}
            },
            "required": ["parcel_id"]
        }
    },
    "query_yakov_handoffs": {
        "name": "query_yakov_handoffs",
        "description": "OMNISCIENCE: pull recent Yakov session handoffs from knowledge_store. Yakov writes one of these at the end of every coding session — they capture summary, changes shipped, files modified, commit hashes, next-session priorities, and any locked doctrine. CALL THIS FIRST when Iosif asks 'what did Yakov do today/this week/recently', 'what shipped', 'what's the latest from Yakov', 'what did the last session do', 'are there any open items from Yakov', or any question about recent technical work. Returns a list of handoff rows with their structured metadata. Each row's `summary`, `changes`, `next_priorities`, and `doctrine_notes` are the most useful fields to surface. For 'today' use hours_back=24; for 'this week' use 168. Use only_high_impact=true when Iosif asks specifically about big shipments or production-level work.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_back": {"type": "integer", "description": "Only return handoffs created within the last N hours. Omit for all time."},
                "instance": {"type": "string", "description": "Filter to one Yakov instance — 'yakov-droplet' (terminal) or 'claude.ai' (chat). Omit for both."},
                "only_high_impact": {"type": "boolean", "default": False, "description": "Only return sessions Iosif marked high_impact (production deploys, doctrine commits, major ships)."},
                "only_with_tags": {"type": "array", "items": {"type": "string"}, "description": "Require all of these tags to be present on the row."},
                "limit": {"type": "integer", "default": 10}
            }
        }
    },
    "query_yakov_commits": {
        "name": "query_yakov_commits",
        "description": "OMNISCIENCE: pull recent git commits from knowledge_store. The post-commit hook records every commit Yakov-on-droplet makes, so this is the authoritative log of what shipped to the codebase. CALL THIS when Iosif asks 'what commits did Yakov make', 'what code shipped today', 'show me recent commits', or wants raw git history. Use this in addition to query_yakov_handoffs when the user wants both the narrative (handoff) and the receipts (commits). Each row gives commit_hash, author, subject (one-line message), branch, and files_changed. For 'today' use hours_back=24.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_back": {"type": "integer", "description": "Only return commits made within the last N hours. Omit for all time."},
                "limit": {"type": "integer", "default": 20}
            }
        }
    },
}

LOCAL_TOOL_FUNCTIONS = {
    "search_knowledge_store": search_knowledge_store,
    "get_knowledge_entry_full": get_knowledge_entry_full,
    "get_active_users": get_active_users,
    "get_tombstoned_users": get_tombstoned_users,
    "get_blocklist": get_blocklist,
    "get_honeypot_hits": get_honeypot_hits,
    "get_recent_yakov_tasks": get_recent_yakov_tasks,
    "lookup_parcel_by_point": lookup_parcel_by_point,
    "fetch_dial_permits": fetch_dial_permits,
    "fetch_dial_sales": fetch_dial_sales,
    "fetch_dial_valuation": fetch_dial_valuation,
    "fetch_dial_dev_docs": fetch_dial_dev_docs,
    "fetch_multco_proptax": fetch_multco_proptax,
    "fetch_multco_records": fetch_multco_records,
    "fetch_multco_sail": fetch_multco_sail,
    "fetch_multnomah_permits": fetch_multnomah_permits,
    "fetch_crook_assessment": fetch_crook_assessment,
    "fetch_crook_permits": fetch_crook_permits,
    "fetch_county_permits": fetch_county_permits,
    "fetch_marion_assessment": fetch_marion_assessment,
    "fetch_marion_records": fetch_marion_records,
    "fetch_marion_permits": fetch_marion_permits,
    "fetch_lane_assessment": fetch_lane_assessment,
    "fetch_lane_records": fetch_lane_records,
    "fetch_lane_permits": fetch_lane_permits,
    "fetch_jackson_assessment": fetch_jackson_assessment,
    "fetch_jackson_records": fetch_jackson_records,
    "fetch_jackson_permits": fetch_jackson_permits,
    "fetch_wash_assessment": fetch_wash_assessment,
    "fetch_wash_records": fetch_wash_records,
    "fetch_washington_permits": fetch_washington_permits,
    "fetch_clack_assessment": fetch_clack_assessment,
    "fetch_clack_records": fetch_clack_records,
    "fetch_clackamas_permits": fetch_clackamas_permits,
    "fetch_linn_assessment": fetch_linn_assessment,
    "fetch_linn_records": fetch_linn_records,
    "fetch_linn_permits": fetch_linn_permits,
    "fetch_benton_assessment": fetch_benton_assessment,
    "fetch_benton_records": fetch_benton_records,
    "fetch_benton_permits": fetch_benton_permits,
    "fetch_yamhill_assessment": fetch_yamhill_assessment,
    "fetch_yamhill_records": fetch_yamhill_records,
    "fetch_yamhill_permits": fetch_yamhill_permits,
    "fetch_polk_assessment": fetch_polk_assessment,
    "fetch_polk_records": fetch_polk_records,
    "fetch_polk_permits": fetch_polk_permits,
    "fetch_douglas_assessment": fetch_douglas_assessment,
    "fetch_douglas_records": fetch_douglas_records,
    "fetch_douglas_permits": fetch_douglas_permits,
    "fetch_josephine_assessment": fetch_josephine_assessment,
    "fetch_josephine_records": fetch_josephine_records,
    "fetch_josephine_permits": fetch_josephine_permits,
    "fetch_clatsop_assessment": fetch_clatsop_assessment,
    "fetch_clatsop_records": fetch_clatsop_records,
    "fetch_clatsop_permits": fetch_clatsop_permits,
    "fetch_tillamook_assessment": fetch_tillamook_assessment,
    "fetch_tillamook_records": fetch_tillamook_records,
    "fetch_tillamook_permits": fetch_tillamook_permits,
    "fetch_lincoln_assessment": fetch_lincoln_assessment,
    "fetch_lincoln_records": fetch_lincoln_records,
    "fetch_lincoln_permits": fetch_lincoln_permits,
    "get_site_plans": get_site_plans,
    "get_ai_spend_today": get_ai_spend_today,
    "query_yakov_handoffs": query_yakov_handoffs,
    "query_yakov_commits": query_yakov_commits,
    "fetch_jefferson_assessment": fetch_jefferson_assessment,
    "fetch_jefferson_records": fetch_jefferson_records,
    "fetch_jefferson_permits": fetch_jefferson_permits,
    "calculate_lot_yield": calculate_lot_yield,
}


# =============================================================================
# CHILD LOADER
# =============================================================================

def _conn():
    return psycopg.connect(DB_DSN, row_factory=dict_row)


def load_child(canonical_name: str) -> Optional[dict]:
    """Load a child config row from child_personas by canonical_name."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM child_personas WHERE canonical_name = %s",
                (canonical_name,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error("load_child(%s) error: %s", canonical_name, e)
        return None


def resolve_tools_for_child(child: dict) -> list[dict]:
    """
    Compute the tool list for a child based on tier + per-child overrides.
    
    Lesson for Nevsky: composition over hardcoding.
    Tier provides defaults. Per-child enabled_tools EXPANDS access (sparingly).
    Per-child disabled_tools REMOVES access (more common — a partner child
    might have all partner-tier tools except web_search if they're untrusted).
    """
    tier = child.get("tier", "staff")
    base_tools = set(TIER_TOOLS.get(tier, []))

    enabled_extra = set(child.get("enabled_tools") or [])
    disabled = set(child.get("disabled_tools") or [])

    final_tool_names = (base_tools | enabled_extra) - disabled

    # Convert tool names to Anthropic API tool dicts
    return [ALL_TOOL_SCHEMAS[name] for name in final_tool_names if name in ALL_TOOL_SCHEMAS]


# =============================================================================
# EXECUTION ENGINE
# =============================================================================

def _execute_local_tool(name: str, tool_input: dict):
    """Execute a local tool (orb_db function) with truncation guard."""
    fn = LOCAL_TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    try:
        result = fn(**tool_input)
        result_json = json.dumps(result, default=str)
        if len(result_json) > MAX_TOOL_RESULT_CHARS:
            return {
                "_truncated": True,
                "_full_size": len(result_json),
                "_note": f"Result truncated to {MAX_TOOL_RESULT_CHARS} chars. Use get_knowledge_entry_full(entry_id) for specifics.",
                "preview": result_json[:MAX_TOOL_RESULT_CHARS],
            }
        return json.loads(result_json)
    except TypeError as e:
        return {"error": f"Tool argument error for {name}: {e}"}
    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return {"error": f"Tool execution error: {e}"}


def _is_rate_limit_error(err) -> bool:
    s = str(err).lower()
    return "rate_limit" in s or "429" in s or "rate limit" in s



def _is_timeout_error(err) -> bool:
    """True if err looks like a request timeout / interrupted call."""
    msg = str(err).lower()
    return any(kw in msg for kw in (
        "timed out",
        "timeout",
        "long-requests",
        "request was cancelled",
        "request cancellation",
        "dropped connection",
    ))

def _friendly_error(err) -> str:
    if _is_rate_limit_error(err):
        return ("I am currently rate-limited (Tier 1 ceiling). "
                "Try again in 60 seconds, or use a shorter question.")
    err_short = str(err)[:120]
    return f"I hit a technical snag. Yakov should check the logs. Quick detail: {err_short}"


def _select_model(child: dict, message: str) -> str:
    """Three tiers: light for trivial, opus for explicit escalation, deep default."""
    msg_lower = message.lower().strip()
    trivial = ["hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "yes", "no", "sure"]
    if msg_lower in trivial or len(msg_lower) < 8:
        return child["model_light"]
    opus_triggers = ["go deep", "think hard", "deeply analyze", "use opus", "opus mode", "deep analysis", "think carefully", "maximum effort"]
    if any(t in msg_lower for t in opus_triggers):
        return child["model_opus"]
    return child["model_deep"]


def _save_insight(child: dict, user_message: str, reply: str):
    """Append Q&A to per-child insight log."""
    try:
        slug = child["canonical_name"]
        path = f"/opt/nevsky-dev/kb/{slug}_profile.jsonl"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "child": slug,
            "bound_user_uuid": str(child.get("bound_user_uuid") or ""),
            "question": user_message,
            "answer": reply,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Could not save insight for %s: %s", child.get("canonical_name"), e)


async def _call_with_fallback(model: str, system: str, messages: list, tools: list, light_model: str):
    """Try requested model; on rate-limit fall back to light_model (Haiku)."""
    try:
        return client.messages.create(
            model=model, max_tokens=2500,
            system=system, messages=messages, tools=tools,
        ), model
    except Exception as e:
        if (_is_rate_limit_error(e) or _is_timeout_error(e)) and model != light_model:
            _err_kind = "rate-limited" if _is_rate_limit_error(e) else "timed-out"
            logger.warning("model=%s %s, falling back to %s", model, _err_kind, light_model)
            return client.messages.create(
                model=light_model, max_tokens=2500,
                system=system, messages=messages, tools=tools,
            ), light_model
        raise


async def ask_child(canonical_name: str, user_message: str, conversation_history: list = None) -> str:
    """
    Main entry point. Load child config, run tool-use loop with fallback, return reply.
    
    Lesson for Nevsky: ONE function does the work for ALL children. Adding a new
    child means inserting a row, not adding a function. Adding a new tool means
    appending to ALL_TOOL_SCHEMAS and TIER_TOOLS, not editing every child file.
    """
    child = load_child(canonical_name)
    if not child:
        return f"Child '{canonical_name}' not found in child_personas table."

    # Honeypot tier returns a generic non-response — design intent.
    if child["tier"] == "honeypot":
        logger.info("honeypot hit: child=%s", canonical_name)
        return "[honeypot: silent]"

    if child.get("decommissioned_at"):
        logger.info("decommissioned child contacted: %s", canonical_name)
        return "[decommissioned]"

    tools = resolve_tools_for_child(child)
    history = conversation_history or []
    messages = history + [{"role": "user", "content": user_message}]
    model = _select_model(child, user_message)
    actual_model = model

    total_input = 0
    total_output = 0
    tool_calls_made = []

    try:
        for iteration in range(MAX_TOOL_ITERATIONS):
            response, actual_model = await _call_with_fallback(
                model, child["system_prompt"], messages, tools, child["model_light"]
            )
            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens

            if response.stop_reason in ("end_turn", "stop_sequence"):
                reply_parts = [b.text for b in response.content if hasattr(b, "type") and b.type == "text"]
                reply = "\n".join(reply_parts).strip() or "I checked but could not find a clear answer."
                logger.info("child=%s model=%s iters=%d in=%d out=%d tools=%s",
                            canonical_name, actual_model, iteration + 1, total_input, total_output, tool_calls_made)
                _save_insight(child, user_message, reply)
                return reply

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_calls_made.append(block.name)
                        if block.name == "web_search":
                            continue
                        result = _execute_local_tool(block.name, block.input or {})
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })
                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
                    continue
                continue

            logger.warning("child=%s unexpected stop_reason: %s", canonical_name, response.stop_reason)
            reply_parts = [b.text for b in response.content if hasattr(b, "type") and b.type == "text"]
            return "\n".join(reply_parts).strip() or "I encountered an unexpected stop. Please try again."

        logger.warning("child=%s hit MAX_TOOL_ITERATIONS=%d", canonical_name, MAX_TOOL_ITERATIONS)
        return "I needed too many lookups to answer this — let's narrow the question."

    except Exception as e:
        logger.error("child=%s error: %s", canonical_name, e)
        return _friendly_error(e)


def is_owner(child: dict, telegram_user_id: str) -> bool:
    """Check if a Telegram user is the bound owner of this child."""
    return str(child.get("bound_telegram_id") or "") == str(telegram_user_id)


def get_intro(canonical_name: str) -> str:
    """Return the intro_message for a child."""
    child = load_child(canonical_name)
    if not child:
        return f"[child '{canonical_name}' not found]"
    return child["intro_message"]
