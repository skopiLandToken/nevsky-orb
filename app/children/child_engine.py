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
    fetch_wash_assessment,
    fetch_wash_records,
    fetch_washington_permits,
    query_yakov_handoffs,
    query_yakov_commits,
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
        "fetch_wash_assessment",
        "fetch_wash_records",
        "fetch_washington_permits",
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
        "fetch_wash_assessment",
        "fetch_wash_records",
        "fetch_washington_permits",
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
    "fetch_wash_assessment": fetch_wash_assessment,
    "fetch_wash_records": fetch_wash_records,
    "fetch_washington_permits": fetch_washington_permits,
    "get_site_plans": get_site_plans,
    "get_ai_spend_today": get_ai_spend_today,
    "query_yakov_handoffs": query_yakov_handoffs,
    "query_yakov_commits": query_yakov_commits,
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
