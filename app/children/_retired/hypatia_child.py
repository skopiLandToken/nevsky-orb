import os
import logging
import json
from anthropic import Anthropic
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

FRED_TELEGRAM_ID = "8087326520"

HYPATIA_SYSTEM_PROMPT = """You are Hypatia, the personal ORB child of Fred Jewell, Chief Operating Officer and Board Member of SKOpi Global Holdings Inc.

You are Fred's executive intelligence layer. You know everything about SKOpi — strategy, products, operations, the Reindeer Project, and the company vision. You speak to Fred professionally, confidently, and with authority. You make him feel the weight and momentum of what SKOpi is building.

FRED'S ROLES AT SKOPI:
- Chief Operating Officer (COO)
- Director of Operations
- Entitlements / Approvals / Development Execution Lead
- Board Member

FRED'S RESPONSIBILITIES:
- Operational execution across all SKOpi initiatives
- Reindeer Project: surveying coordination, engineering coordination, entitlement process, development execution
- Board governance and oversight
- Approvals and development readiness

SKOPI PRODUCT PORTFOLIO:
1. SKOpi Token Platform (app.skopi.io) — LIVE — Real asset-backed token raises on Solana
2. Nevsky ORB (nevsky.skopi.io) — LIVE — AI organizational intelligence OS
3. ORB Children Platform — BUILDING — Personal AI children for every executive
4. Land Development Engine — BUILDING — Reindeer Project Phase 1
5. SKOpi Spinoff Platform — PLANNED — Launch companies inside SKOpi ecosystem
6. SKOpi Marketplace — PLANNED — Token secondary market
7. Marketing Partner Network — LIVE — 3-tier referral (8%/3%/1%) paid in USDC
8. SKOpi Communications App CCN — PHASE 3-4 — Private self-hosted comms
9. Private Device Program — PHASE 3-4 — GrapheneOS on Pixel for all executives
10. Vesna Social Media Child — PHASE 3 — ORB child managing all public channels

COMPANY CONTEXT:
- SKOpi Global Holdings Inc. — Delaware corporation formed March 20 2026
- CEO/Chairman/President: Iosif Y Skorohodov
- CSO/Board Member: Daniel Roy Wikert
- COO/Board Member: Fred Jewell
- Infrastructure: self-hosted at skopi.io domains — no Google, no Microsoft, no Apple
- Token: SKOPI on Solana blockchain
- Office environment: office.skopi.io (Nextcloud + Collabora)
- Reindeer Project: land development opportunity in Redmond Oregon

REINDEER PROJECT STATUS:
- Early-stage land development opportunity in Redmond Oregon
- $3,000 earnest money deposit in place with 90-day due diligence period
- Requires: surveying, engineering coordination, entitlement process, city pre-dev application
- Bonneville Power approval needed for plat advancement
- Drone shoot scheduled Tuesday April 28 with Timothy Park
- This is Fred's primary operational domain

BEHAVIOR:
- Speak to Fred as a peer executive — he is COO, treat him accordingly
- Be impressive and forward-looking — show him the momentum and scale of what is being built
- Answer questions about operations, Reindeer Project, products, and company strategy
- Web search enabled — use it for current information when needed
- Keep responses tight and executive-level
- Tone: professional, authoritative, aspirational"""

HYPATIA_INTRO = """Hi Fred — I'm Hypatia, your personal SKOpi ORB assistant.

I have full executive access to SKOpi intelligence as your COO interface:
- Reindeer Project status and operational pipeline
- Full product portfolio and company strategy
- Board-level intelligence and decisions
- SKOpi infrastructure and systems

What do you need?"""


def is_fred(telegram_user_id: str) -> bool:
    return str(telegram_user_id) == FRED_TELEGRAM_ID


def _select_model(message: str) -> str:
    complex_keywords = ["reindeer", "project", "strategy", "explain", "how", "what",
                        "product", "full", "detail", "plan", "status", "update"]
    if any(kw in message.lower() for kw in complex_keywords):
        return os.environ.get("NEVSKY_SUMMARY_MODEL_STRONG", "claude-sonnet-4-6")
    return os.environ.get("NEVSKY_SUMMARY_MODEL_CHEAP", "claude-haiku-4-5-20251001")


async def ask_hypatia(user_message: str, conversation_history: list = None) -> str:
    history = conversation_history or []
    messages = history + [{"role": "user", "content": user_message}]
    model = _select_model(user_message)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            system=HYPATIA_SYSTEM_PROMPT,
            messages=messages,
            # tools=[{"type": "web_search_20250305", "name": "web_search"}],  # DISABLED temporarily
        )
        reply_parts = []
        for block in response.content:
            if hasattr(block, "type") and block.type == "text":
                reply_parts.append(block.text)
        reply = "\n".join(reply_parts).strip() or "I could not find a clear answer."
        logger.info("hypatia model=%s in=%d out=%d",
                    model, response.usage.input_tokens, response.usage.output_tokens)
        return reply
    except Exception as e:
        logger.error("Hypatia error: %s", e)
        return "Hypatia encountered an error. Please try again."


def get_intro() -> str:
    return HYPATIA_INTRO

FRED_EMAIL = "fred.jewell@skopi.io"
WELCOME_SENT_FLAG = "/opt/nevsky-dev/kb/fred_welcome_sent.flag"

WELCOME_EMAIL_SUBJECT = "🚀 Welcome to SKOpi, Fred — You're In"

WELCOME_EMAIL_BODY = """Hi Fred,

Welcome to SKOpi Global Holdings Inc. 🎉

You're now officially wired into the ORB — our organizational intelligence system.

🤖 YOUR PERSONAL AI — HYPATIA
You're already talking to her. Ask her anything, anytime, on Telegram @hypatia_skopi_bot.

💻 YOUR OFFICE — office.skopi.io
Username: fred.jewell | Temp password: Welcome2SKOpi!
✅ Email, Deck board, Calendar, Document editing — all live.

🏗️ YOUR MISSION — REINDEER PROJECT (your operational domain)
- Surveying + engineering coordination
- Entitlement process + city pre-dev application
- Bonneville Power approval
- Drone shoot: Tuesday April 28, 3-4 PM (Timothy Park)

🌐 10 PRODUCTS WE'RE BUILDING
1. 🪙 SKOpi Token Platform — LIVE
2. 🧠 Nevsky ORB — LIVE
3. 👶 ORB Children (Sophia, Ophelia, Hypatia) — BUILDING
4. 🏗️ Land Development Engine (Reindeer) — BUILDING
5. 🚀 Spinoff Platform — PLANNED
6. 🛒 SKOpi Marketplace — PLANNED
7. 🤝 Marketing Partner Network 8%/3%/1% USDC — LIVE
8. 📱 Communications App CCN — PHASE 3-4
9. 📱 Private Device Program (GrapheneOS) — PHASE 3-4
10. 📣 Vesna Social Media Child — PHASE 3

📋 YOUR KANBAN BOARD — LIVE NOW
https://office.skopi.io/apps/deck/#/board/2
Every active project card, timeline, and milestone is there. Your Reindeer Project cards are waiting for you.

📱 PRIVATE DEVICE PROGRAM — COMING PHASE 3-4
SKOpi is building a fully private mobile stack for all executives. We're moving to GrapheneOS on Google Pixel hardware — no Google Play Services, no Apple, no carrier surveillance. Every call, message, and app runs on our own infrastructure. Executives get a hardened private intelligence terminal instead of a smartphone. Want to know more about how this works and what it means for you? Just ask Hypatia.

✅ FIRST STEPS
1. Log into office.skopi.io — change your password
2. Open your Deck board: https://office.skopi.io/apps/deck/#/board/2
3. Review your Reindeer Project cards — those are yours to drive
4. Ask Hypatia on Telegram anything you need
5. Schedule your first briefing with Iosif

Welcome to the team, Fred. You're coming in at a moment that matters.

Iosif Y Skorohodov
CEO | SKOpi Global Holdings Inc.
iosif@skopi.io"""


async def maybe_send_welcome(chat_id: int):
    """Send welcome email and Telegram message on first contact."""
    import os, httpx
    if os.path.exists(WELCOME_SENT_FLAG):
        return
    # Mark as sent immediately to prevent duplicates
    open(WELCOME_SENT_FLAG, 'w').write('sent')
    # Send welcome Telegram message
    bot_token = os.environ.get("HYPATIA_BOT_TOKEN", "")
    if bot_token:
        welcome_tg = """🚀 *Welcome to SKOpi, Fred!*

I'm Hypatia — your personal ORB intelligence. I have full executive access to SKOpi's operations, strategy, and the Reindeer Project.

Here's what just went live for you:
- 📧 fred.jewell@skopi.io — your executive email
- 💻 office.skopi.io — your full workspace (Deck, Mail, Docs)
- 🏗️ Reindeer Project board — your operational domain
- 🤖 Me — available 24/7 for anything you need
• 📋 Your Deck board: https://office.skopi.io/apps/deck/#/board/2

📱 *Coming Phase 3-4: SKOpi Private Device Program*
We're building a fully private mobile stack for all executives — GrapheneOS on Pixel hardware, no Google, no Apple, no carrier in the chain. Want to know more? Just ask me.


A welcome email with full details is headed to your inbox right now.

What do you need first?"""
        async with httpx.AsyncClient() as c:
            await c.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": welcome_tg, "parse_mode": "Markdown"},
                timeout=10
            )
    return  # EMAIL DISABLED until re-enabled
    # Send welcome email via Resend
    resend_key = os.environ.get("RESEND_API_KEY", "")
    if resend_key:
        try:
            async with httpx.AsyncClient() as c:
                await c.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                    json={
                        "from": "Iosif Skorohodov <iosif@skopi.io>",
                        "to": [FRED_EMAIL],
                        "subject": WELCOME_EMAIL_SUBJECT,
                        "text": WELCOME_EMAIL_BODY
                    },
                    timeout=15
                )
            logger.info("Fred welcome email sent")
        except Exception as e:
            logger.error("Fred welcome email failed: %s", e)
