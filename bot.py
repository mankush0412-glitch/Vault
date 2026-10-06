"""
╔══════════════════════════════════════════════════╗
║          NEXT LEVEL VAULT BOT                    ║
║   Telegram Account (OTP) Seller Bot              ║
║   Final – Fast + Razorpay + Self-Ping           ║
║   Run:  python bot.py                           ║
╚══════════════════════════════════════════════════╝
"""

import os
import io
import re
import uuid
import asyncio
import contextlib
import zipfile
import tempfile
import hashlib
import logging
import hmac
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from telethon import TelegramClient, events, Button, functions, types
from telethon.sessions import StringSession
from telethon.errors import (
    UserNotParticipantError,
    ChatAdminRequiredError,
    ChannelPrivateError,
    FloodWaitError,
)
from account_manager import AccountManager
from vnh_server import VNHServer
import aiohttp
import time
import html as _html
from urllib.parse import quote as _urlquote

# ─── 1. ENV & CONFIG ────────────────────────────────────────────
load_dotenv()

API_ID    = int(os.getenv("API_ID", "0"))
API_HASH  = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
OWNER_ID  = int(os.getenv("OWNER_ID", "0"))
DB_NAME   = os.getenv("DB_NAME", "stark_bot")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_API_BASE = "https://api.razorpay.com/v1"
LOGS_CHANNEL_ID = int(os.getenv("LOGS_CHANNEL_ID", "0") or "0")
RENDER_EXTERNAL_URL = os.getenv("PUBLIC_URL", "") or os.getenv("RENDER_EXTERNAL_URL", "")  # works the same on Render, AWS, or any host — just set PUBLIC_URL to wherever this is reachable
VNH_API_KEY  = os.getenv("VNH_API_KEY", "")
VNH_API_BASE = os.getenv("VNH_API_BASE", "https://api.vnhotp.com")
VNH_MARKUP_PERCENT = float(os.getenv("VNH_MARKUP_PERCENT", "20"))

ADMIN_IDS: List[int] = [OWNER_ID] if OWNER_ID else []
for _a in os.getenv("ADMIN_IDS", "").split(","):
    try:
        _id = int(_a.strip())
        if _id not in ADMIN_IDS:
            ADMIN_IDS.append(_id)
    except ValueError:
        pass

_fj_raw = os.getenv("FORCE_JOIN_CHAT_IDS", os.getenv("FORCE_JOIN_CHAT_ID", "")).strip()
RAW_CHAT_IDS: List[str] = [x.strip() for x in _fj_raw.split(",") if x.strip()]

if not all([API_ID, API_HASH, BOT_TOKEN, OWNER_ID]):
    raise ValueError("❌ .env incomplete!  Set API_ID, API_HASH, BOT_TOKEN, OWNER_ID.")

# ─── 2. LOGGING ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("NextLevelVault")

# ─── 3. MONGODB ──────────────────────────────────────────────────
_mongo_client    = AsyncIOMotorClient(MONGO_URL)
db               = _mongo_client[DB_NAME]
accounts_col     = db["accounts"]
users_col        = db["users"]
orders_col       = db["orders"]
deposits_col     = db["deposits"]
settings_col     = db["settings"]
countries_col    = db["countries"]
bot_admins_col   = db["bot_admins"]
categories_col   = db["categories"]
packages_col     = db["packages"]

# ─── 4. BOT INSTANCE ────────────────────────────────────────────
_session_name = "next_level_vault_" + hashlib.md5(BOT_TOKEN.encode()).hexdigest()[:8]
bot = TelegramClient(_session_name, API_ID, API_HASH)

pending_otp_requests: Dict = {}
user_states: Dict           = {}
acc_mgr: Optional[AccountManager] = None

# ─── 4b. VNH SUPPLIER API (Server 2) ────────────────────────────
def now_ist():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

_usd_inr: float = 90.0
_usd_fetched: float = 0

async def get_usd_inr() -> float:
    global _usd_inr, _usd_fetched
    if time.time() - _usd_fetched < 3600:
        return _usd_inr
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://open.er-api.com/v6/latest/USD",
                              timeout=aiohttp.ClientTimeout(total=5)) as r:
                d = await r.json()
                if d.get("result") == "success":
                    _usd_inr = float(d["rates"]["INR"])
                    _usd_fetched = time.time()
    except Exception as e:
        logging.warning(f"USD/INR fetch failed, using {_usd_inr}: {e}")
    return _usd_inr

vnh_server = VNHServer(
    api_key=VNH_API_KEY,
    base_url=VNH_API_BASE,
    settings_col=settings_col,
    get_usd_inr=get_usd_inr,
    now_ist=now_ist,
    default_markup_percent=VNH_MARKUP_PERCENT,
)

# ─── 5. DEFAULT DATA ────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "bot_name":            "Prime Vault",
    "upi_id":              "",
    "upi_name":            "Prime Vault",
    "support_link":        "",
    "referral_bonus":      0,
    "referral_percent":    0,
    "min_deposit":         10.0,
    "welcome_photo":       "",
    "whatsapp_enabled":    False,
    "auto_verify_delay":   10,
    "payment_webhook_url": "",
    "default_2fa":         "",
    "server_count":        2,
    "vnh_server_number":   2,
    "logs_channel_id":     0,
    "terms_text":          "",
}

DEFAULT_CATEGORIES = [
    {"name": "Fresh Accounts", "icon": "🆕", "order": 1},
    {"name": "Cheap Accounts", "icon": "💰", "order": 2},
    {"name": "Old Accounts",   "icon": "📅", "order": 3},
    {"name": "Spam Accounts",  "icon": "📧", "order": 4},
    {"name": "Rare Accounts",  "icon": "⭐", "order": 5},
    {"name": "Number Change",  "icon": "🔄", "order": 6},
]

DEFAULT_PACKAGES = [
    {"credits": 10, "price": 10.0, "usdt": 0.11, "stars": 10},
    {"credits": 25, "price": 25.0, "usdt": 0.27, "stars": 25},
    {"credits": 50, "price": 50.0, "usdt": 0.55, "stars": 50},
    {"credits": 100, "price": 100.0, "usdt": 1.10, "stars": 100},
]

DEFAULT_COUNTRIES = [
    {"code": "IN", "name": "India",       "flag": "🇮🇳", "price": 30.0},
    {"code": "BD", "name": "Bangladesh",  "flag": "🇧🇩", "price": 28.0},
    {"code": "PK", "name": "Pakistan",    "flag": "🇵🇰", "price": 25.0},
    {"code": "NG", "name": "Nigeria",     "flag": "🇳🇬", "price": 20.0},
    {"code": "ID", "name": "Indonesia",   "flag": "🇮🇩", "price": 28.0},
    {"code": "US", "name": "USA",         "flag": "🇺🇸", "price": 50.0},
    {"code": "VN", "name": "Vietnam",     "flag": "🇻🇳", "price": 22.0},
    {"code": "MM", "name": "Myanmar",     "flag": "🇲🇲", "price": 20.0},
    {"code": "KE", "name": "Kenya",       "flag": "🇰🇪", "price": 22.0},
    {"code": "CO", "name": "Colombia",    "flag": "🇨🇴", "price": 25.0},
    {"code": "ZW", "name": "Zimbabwe",    "flag": "🇿🇼", "price": 18.0},
    {"code": "GB", "name": "UK",          "flag": "🇬🇧", "price": 45.0},
    {"code": "RU", "name": "Russia",      "flag": "🇷🇺", "price": 30.0},
    {"code": "BR", "name": "Brazil",      "flag": "🇧🇷", "price": 25.0},
    {"code": "PH", "name": "Philippines", "flag": "🇵🇭", "price": 22.0},
    {"code": "EG", "name": "Egypt",       "flag": "🇪🇬", "price": 20.0},
]

COUNTRY_FLAGS: Dict[str, str] = {c["code"]: c["flag"] for c in DEFAULT_COUNTRIES}
COUNTRY_FLAGS.update({
    "AU": "🇦🇺", "CA": "🇨🇦", "TR": "🇹🇷", "DE": "🇩🇪", "FR": "🇫🇷",
    "IT": "🇮🇹", "ES": "🇪🇸", "MX": "🇲🇽", "AR": "🇦🇷", "TH": "🇹🇭",
    "UA": "🇺🇦", "SA": "🇸🇦", "AE": "🇦🇪", "JP": "🇯🇵", "KR": "🇰🇷",
    "CN": "🇨🇳", "IR": "🇮🇷", "IQ": "🇮🇶", "MA": "🇲🇦", "UZ": "🇺🇿",
    "KZ": "🇰🇿", "NP": "🇳🇵", "LK": "🇱🇰", "SG": "🇸🇬", "MY": "🇲🇾",
})

# ─── 6. DATABASE INIT ────────────────────────────────────────────
async def init_db():
    await users_col.create_index("user_id", unique=True)
    await accounts_col.create_index([("country_code", 1), ("status", 1)])
    await deposits_col.create_index("user_id")
    await orders_col.create_index("user_id")
    await settings_col.create_index("key", unique=True)
    await countries_col.create_index("code", unique=True)
    await bot_admins_col.create_index("telegram_id", unique=True)
    await categories_col.create_index("name", unique=True)
    await packages_col.create_index("credits", unique=True)

    for key, val in DEFAULT_SETTINGS.items():
        if not await settings_col.find_one({"key": key}):
            await settings_col.insert_one({"key": key, "value": val})

    for c in DEFAULT_COUNTRIES:
        if not await countries_col.find_one({"code": c["code"]}):
            await countries_col.insert_one({**c, "is_active": True})

    for cat in DEFAULT_CATEGORIES:
        if not await categories_col.find_one({"name": cat["name"]}):
            await categories_col.insert_one({**cat, "is_active": True})

    for pkg in DEFAULT_PACKAGES:
        if not await packages_col.find_one({"credits": pkg["credits"]}):
            await packages_col.insert_one(pkg)

    log.info("✅ Database initialised")

# ─── 7. SETTINGS CACHE ──────────────────────────────────────────
_settings_cache: Dict   = {}
_cache_ts: Optional[datetime] = None
_CACHE_TTL = 60

async def get_setting(key: str, default=None):
    global _settings_cache, _cache_ts
    now = datetime.utcnow()
    if _cache_ts is None or (now - _cache_ts).total_seconds() > _CACHE_TTL:
        _settings_cache = {}
        async for doc in settings_col.find({}):
            _settings_cache[doc["key"]] = doc["value"]
        _cache_ts = now
    return _settings_cache.get(key, default)

async def set_setting(key: str, value):
    global _settings_cache, _cache_ts
    await settings_col.update_one(
        {"key": key},
        {"$set": {"key": key, "value": value, "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    _settings_cache = {}
    _cache_ts = None

# ─── 8. ADMIN HELPERS ────────────────────────────────────────────
async def get_all_admin_ids() -> List[int]:
    ids = list(ADMIN_IDS)
    async for a in bot_admins_col.find({"is_active": True}):
        if a["telegram_id"] not in ids:
            ids.append(a["telegram_id"])
    return ids

async def is_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    doc = await bot_admins_col.find_one({"telegram_id": user_id, "is_active": True})
    return doc is not None

# ─── 9. BOT USERNAME CACHE ──────────────────────────────────────
_bot_username: Optional[str] = None
async def get_bot_username() -> str:
    global _bot_username
    if not _bot_username:
        me = await bot.get_me()
        _bot_username = me.username
    return _bot_username or ""

# ─── 10. FORCE‑JOIN ─────────────────────────────────────────────
def _parse_chat_id(raw: str):
    raw = raw.strip()
    if raw.startswith("@"):
        return raw
    try:
        return int(raw)
    except ValueError:
        return None

async def _is_member_of(chat_raw: str, user_id: int) -> bool:
    parsed = _parse_chat_id(chat_raw)
    if parsed is None:
        return False
    try:
        entity = await bot.get_entity(parsed)
        await bot.get_permissions(entity, user_id)
        return True
    except UserNotParticipantError:
        return False
    except (ChatAdminRequiredError, ChannelPrivateError):
        return True
    except Exception:
        return False

async def is_user_member(user_id: int) -> bool:
    if not RAW_CHAT_IDS:
        return True
    for raw in RAW_CHAT_IDS:
        if not await _is_member_of(raw, user_id):
            return False
    return True

async def send_join_message(event):
    buttons = []
    for raw in RAW_CHAT_IDS:
        if await _is_member_of(raw, event.sender_id):
            continue
        title = raw
        try:
            parsed = _parse_chat_id(raw)
            entity = await bot.get_entity(parsed)
            title = getattr(entity, "title", raw)
        except Exception:
            pass
        if raw.startswith("@"):
            buttons.append([Button.url(f"📢 Join {title}", f"https://t.me/{raw[1:]}")])
        else:
            link = None
            try:
                res = await bot(functions.messages.ExportChatInviteRequest(
                    peer=entity, expire_date=None, usage_limit=0))
                link = res.link
            except Exception:
                pass
            if link:
                buttons.append([Button.url(f"📢 Join {title}", link)])
    if not buttons:
        return
    buttons.append([Button.inline("✅ Check Again", b"check_join")])
    await event.respond(
        fancy("⚠️ **You must join the channel(s) below to use this bot.**"),
        buttons=buttons,
    )

# ─── 11. USER HELPER ────────────────────────────────────────────
async def get_or_create_user(user_id: int,
                              referrer_id: Optional[int] = None) -> dict:
    user = await users_col.find_one({"user_id": user_id})
    if not user:
        import random, string
        ref_code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        user = {
            "user_id":           user_id,
            "balance":           0.0,
            "withdrawable":      0.0,
            "referral_code":     ref_code,
            "referred_by":       referrer_id,
            "referral_earnings": 0.0,
            "is_banned":         False,
            "joined_at":         datetime.utcnow(),
            "language":          "en",
            "tier":              "⭐",
        }
        await users_col.insert_one(user)
        await log_event(f"🆕 **ɴᴇᴡ ᴜsᴇʀ**\n• ᴜsᴇʀ: `{user_id}`" + (f"\n• ʀᴇғᴇʀʀᴇᴅ ʙʏ: `{referrer_id}`" if referrer_id else ""))
        if referrer_id and referrer_id != user_id:
            bonus = float(await get_setting("referral_bonus", 0))
            if bonus > 0:
                await users_col.update_one(
                    {"user_id": referrer_id},
                    {"$inc": {"balance": bonus, "referral_earnings": bonus,
                              "withdrawable": bonus}},
                )
                try:
                    await bot.send_message(
                        referrer_id,
                        fancy(f"🎁 **Referral bonus!**\n+₹{bonus:.0f} credited."),
                    )
                except Exception:
                    pass
    return user

# ─── 12. COUNTRIES ──────────────────────────────────────────────
async def get_active_countries(category: str = None, server: int = 1, year: str = None) -> List[dict]:
    result = []
    async for c in countries_col.find({"is_active": True}):
        stock_query = {"country_code": c["code"], "status": "available"}
        if category:
            stock_query["category"] = category
        if year:
            stock_query["year"] = str(year)
        if server == 1:
            # legacy stock (uploaded before the server field existed) counts as Server 1
            stock_query["$or"] = [{"server": 1}, {"server": {"$exists": False}}]
        else:
            stock_query["server"] = server
        stock = await accounts_col.count_documents(stock_query)
        if stock > 0:
            result.append({
                "code":  c["code"],
                "name":  c["name"],
                "flag":  c["flag"],
                "price": c["price"],
                "stock": stock,
            })
    return result

async def get_categories() -> List[dict]:
    cats = []
    async for cat in categories_col.find({"is_active": True}).sort("order", 1):
        cats.append(cat)
    return cats

# ─── 13. SESSION ZIP CONVERSION ────────────────────────────────
def _phone_from_filename(name: str) -> str:
    base   = os.path.splitext(os.path.basename(name))[0]
    digits = re.sub(r"[^\d]", "", base)
    return f"+{digits}" if len(digits) >= 7 else base

def _detect_session_format(session_bytes: bytes):
    import sqlite3, struct, ipaddress as _ip
    _DC_IP = {
        1: "149.154.175.53",
        2: "149.154.167.51",
        3: "149.154.175.100",
        4: "149.154.167.91",
        5: "91.108.56.130",
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".session", delete=False)
    try:
        tmp.write(session_bytes)
        tmp.flush()
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        cur  = conn.cursor()
        cur.execute("PRAGMA table_info(sessions)")
        cols = {row[1] for row in cur.fetchall()}
        if "server_address" in cols:
            cur.execute("SELECT dc_id, server_address, port, auth_key FROM sessions")
            row = cur.fetchone()
            conn.close()
            if row and row[3]:
                return "telethon", row[0], row[1], row[2], row[3]
        elif "user_id" in cols or "api_id" in cols:
            cur.execute("SELECT dc_id, auth_key FROM sessions")
            row = cur.fetchone()
            conn.close()
            if row and row[1]:
                dc_id  = row[0]
                server = _DC_IP.get(dc_id, _DC_IP[2])
                return "pyrogram", dc_id, server, 443, row[1]
        else:
            conn.close()
    except Exception as e:
        log.warning(f"[detect_fmt] sqlite error: {e}")
    finally:
        with contextlib.suppress(Exception):
            os.unlink(tmp.name)
    return None, None, None, None, None

def _pyrogram_to_telethon_ss(dc_id: int, server: str,
                              port: int, auth_key: bytes) -> Optional[str]:
    import struct, base64, ipaddress as _ip
    try:
        if not isinstance(auth_key, bytes) or len(auth_key) != 256:
            return None
        ip_bytes = _ip.ip_address(server).packed
        payload  = (
            struct.pack(">B", dc_id) +
            ip_bytes +
            struct.pack(">H", port) +
            auth_key
        )
        return "1" + base64.urlsafe_b64encode(payload).decode()
    except Exception as e:
        log.warning(f"[pyro→ss] build failed: {e}")
        return None

async def _session_file_to_string(session_bytes: bytes) -> Optional[str]:
    fmt, dc_id, server, port, auth_key = _detect_session_format(session_bytes)
    if fmt is None:
        return None
    ss = _pyrogram_to_telethon_ss(dc_id, server, port, auth_key)
    if ss:
        log.info(f"[zip] ✅ {fmt} session → StringSession")
    else:
        log.warning(f"[zip] ❌ Could not build StringSession for {fmt} session")
    return ss

# ─── 14. UPI QR ──────────────────────────────────────────────────
def _make_upi_qr(upi_id: str, amount: float, name: str) -> Optional[bytes]:
    try:
        import qrcode as qrc
        uri = (f"upi://pay?pa={upi_id}&pn={name}"
               f"&am={amount:.2f}&cu=INR&tn=NextLevelVaultDeposit")
        buf = io.BytesIO()
        qrc.make(uri).save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        log.warning(f"[QR] Failed to generate UPI QR: {e}")
        return None

def _make_polished_qr(upi_id: str, amount: float, name: str) -> Optional[bytes]:
    """A nicer-looking card (white background, header/footer bands, centered QR) —
    generated locally with Pillow, no external branding/logos."""
    try:
        import qrcode as qrc
        from PIL import Image, ImageDraw, ImageFont
        uri = f"upi://pay?pa={upi_id}&pn={name}&am={amount:.2f}&cu=INR&tn=PrimeVaultDeposit"
        qr_img = qrc.make(uri).convert("RGB")
        qs = qr_img.size[0]
        pad, header_h, footer_h = 40, 90, 70
        W = qs + pad * 2
        H = header_h + qs + footer_h
        card = Image.new("RGB", (W, H), "white")
        draw = ImageDraw.Draw(card)
        draw.rectangle([0, 0, W, header_h], fill=(25, 45, 95))
        try:
            font_h = ImageFont.truetype("DejaVuSans-Bold.ttf", 26)
            font_f = ImageFont.truetype("DejaVuSans.ttf", 18)
        except Exception:
            font_h = font_f = ImageFont.load_default()
        title = "SCAN & PAY"
        tw = draw.textlength(title, font=font_h)
        draw.text(((W - tw) / 2, 28), title, font=font_h, fill="white")
        card.paste(qr_img, (pad, header_h))
        foot = "GPay  •  PhonePe  •  Paytm  •  BHIM"
        fw = draw.textlength(foot, font=font_f)
        draw.text(((W - fw) / 2, header_h + qs + 22), foot, font=font_f, fill=(60, 60, 60))
        buf = io.BytesIO()
        card.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        log.warning(f"[QR] Polished QR failed, falling back: {e}")
        return _make_upi_qr(upi_id, amount, name)

# ─── 14b. REAL RAZORPAY DYNAMIC UPI QR (Auto-Verify) ────────────
async def razorpay_create_qr(amount: float, dep_id: str, user_id: int) -> Optional[dict]:
    """Creates a single-use, fixed-amount Razorpay UPI QR for one deposit.
    Returns the Razorpay QR entity (has 'id', 'image_url', 'short_url'),
    or None if Razorpay isn't configured / the request failed."""
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return None
    payload = {
        "type": "upi_qr",
        "name": f"Deposit {dep_id}",
        "usage": "single_use",
        "fixed_amount": True,
        "payment_amount": int(round(amount * 100)),
        "description": "Wallet top-up",
        "close_by": int(time.time()) + 1800,  # 30 min validity
        "notes": {"deposit_id": str(dep_id), "user_id": str(user_id)},
    }
    try:
        async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) as s:
            async with s.post(f"{RAZORPAY_API_BASE}/payments/qr_codes", json=payload,
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
                body = await r.json()
                if r.status not in (200, 201):
                    log.error(f"[Razorpay] QR create failed [{r.status}]: {body}")
                    return None
                return body
    except Exception as e:
        log.error(f"[Razorpay] QR create error: {e}")
        return None

async def razorpay_close_qr(qr_id: str):
    """Best-effort close so a paid/expired QR can't be reused."""
    if not qr_id or not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return
    try:
        async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) as s:
            async with s.post(f"{RAZORPAY_API_BASE}/payments/qr_codes/{qr_id}/close",
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status not in (200, 201):
                    log.warning(f"[Razorpay] QR close failed [{r.status}] for {qr_id}")
    except Exception as e:
        log.warning(f"[Razorpay] QR close error for {qr_id}: {e}")

async def log_event(text: str):
    """Sends an event line to the admin log channel, if configured."""
    channel_id = LOGS_CHANNEL_ID or int(await get_setting("logs_channel_id", 0) or 0)
    if not channel_id:
        return
    try:
        await bot.send_message(channel_id, fancy(text))
    except Exception as e:
        log.warning(f"[log_channel] send failed: {e}")

# ─── 14c. TEMP-MESSAGE CLEANUP + UPI-APP PAY LINKS ─────────────
user_temp_msgs: Dict[int, List[int]] = {}
_KEEP_TEMP_ON = {"upi_app", "check_payment", "deposit_paid", "store_noop"}

def _track_temp(user_id: int, msg) -> None:
    """Remember a helper message (QR image, info note) so it can be deleted later."""
    mid = getattr(msg, "id", None)
    if mid:
        user_temp_msgs.setdefault(user_id, []).append(mid)

async def _cleanup_temp(user_id: int) -> None:
    ids = user_temp_msgs.pop(user_id, [])
    if ids:
        try:
            await bot.delete_messages(user_id, ids)
        except Exception as e:
            log.debug(f"[cleanup] could not delete {ids}: {e}")

async def _delete_msg(user_id: int, msg_id) -> None:
    if not msg_id:
        return
    try:
        await bot.delete_messages(user_id, msg_id)
    except Exception:
        pass

async def _pay_app_button(user_id: int, link):
    """'Pay via UPI app' — a real URL button (opens the phone's UPI app through our
    /pay redirect page) when a public URL + upi:// link exist, else a callback fallback."""
    if link and str(link).startswith("upi://") and RENDER_EXTERNAL_URL:
        token = uuid.uuid4().hex[:16]
        try:
            await db["pay_links"].create_index("created_at", expireAfterSeconds=3600)
            await db["pay_links"].insert_one({"token": token, "link": link, "created_at": datetime.utcnow()})
            return Button.url(button_label("📲 ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ"), f"{RENDER_EXTERNAL_URL.rstrip('/')}/pay/{token}")
        except Exception as e:
            log.warning(f"[pay_link] could not create link: {e}")
    return color_btn("📲 ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ", "upi_app", "primary")

async def _deposit_buttons(user_id: int, link) -> list:
    return [
        [color_btn("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ", "deposit_paid", "success")],
        [color_btn("❌ ᴄᴀɴᴄᴇʟ", "cancel_payment", "danger")],
        [color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")],
    ]

async def razorpay_qr_paid(qr_id: str, amount: float) -> bool:
    """Live check with Razorpay: has this single-use QR received the exact amount?"""
    if not qr_id or not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return False
    try:
        async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) as s_:
            async with s_.get(f"{RAZORPAY_API_BASE}/payments/qr_codes/{qr_id}/payments",
                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    log.warning(f"[Razorpay] QR payments lookup [{r.status}] for {qr_id}")
                    return False
                body = await r.json()
        want = int(round(float(amount) * 100))
        return any(i.get("status") == "captured" and int(i.get("amount", 0)) == want
                   for i in body.get("items", []))
    except Exception as e:
        log.error(f"[Razorpay] QR payments lookup error: {e}")
        return False

async def _verify_deposit_ui(event, user_id: int) -> None:
    """'I Have Paid' / 'Check Payment' — verifies for real, never blind-approves."""
    from bson import ObjectId
    st = user_states.get(user_id, {})
    if st.get("state") == "await_deposit_screenshot":
        await event.answer("📸 Pay first, then send the payment screenshot here in the chat.", alert=True)
        return
    dep_id = st.get("deposit_id_obj")
    if not dep_id:
        await event.answer("❌ No active deposit.", alert=True)
        return
    dep = await deposits_col.find_one({"_id": ObjectId(dep_id)})
    if not dep:
        await event.answer("❌ Deposit not found.", alert=True)
        return
    if dep.get("status") == "pending" and dep.get("qr_code_id"):
        if await razorpay_qr_paid(dep["qr_code_id"], dep["amount"]):
            await _credit_deposit_by_id(dep["_id"], notify=False, delete_ui=False)
            dep = await deposits_col.find_one({"_id": dep["_id"]})
    status = dep.get("status")
    if status == "approved":
        await _cleanup_temp(user_id)
        user_states.pop(user_id, None)
        await event.edit(
            fancy(f"✅ **Payment Confirmed!**\n\n`{dep.get('credits', 0)} Credits` Added To Your Wallet."),
            buttons=[[color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]]
        )
    elif status == "pending":
        await event.answer("⏳ Payment not received yet. Wait 1–2 minutes and tap again.", alert=True)
    else:
        await event.answer(f"Status: {status}", alert=True)

async def _blocking_pending_deposit(user_id: int):
    """Returns the user's pending deposit (auto or manual) if it's still within its
    15-minute window (and auto-expires it otherwise), so a user can't open two at once."""
    dep = await deposits_col.find_one(
        {"user_id": user_id, "status": "pending", "method": {"$in": ["razorpay_auto", "manual"]}},
        sort=[("_id", -1)]
    )
    if not dep:
        return None
    age = datetime.utcnow() - dep.get("created_at", datetime.utcnow())
    if age > timedelta(minutes=15):
        await deposits_col.update_one({"_id": dep["_id"]}, {"$set": {"status": "expired"}})
        if dep.get("qr_code_id"):
            await razorpay_close_qr(dep["qr_code_id"])
        return None
    return dep

async def _show_pending_deposit_block(event, dep):
    submitted = dep.get("created_at", datetime.utcnow())
    await event.edit(
        fancy("⏳ **You Already Have A Pending Deposit**\n\n")
        + fancy_line("• amount: ") + f"`₹{dep['amount']:.2f}`\n"
        + fancy_line("• txn id: ") + f"`{dep.get('qr_code_id') or str(dep['_id'])}`\n"
        + fancy_line("• submitted: ") + f"`{submitted.strftime('%d/%m/%Y %H:%M')}`\n\n"
        + fancy_line("please wait for it to be approved. if you made a mistake and want to submit a new one, cancel this first."),
        buttons=[
            [color_btn("❌ ᴄᴀɴᴄᴇʟ ᴛʜɪs ᴅᴇᴘᴏsɪᴛ", "cancel_payment", "danger")],
            [color_btn("◀️ ʙᴀᴄᴋ", "deposit", "primary")],
        ]
    )

async def _start_razorpay_auto_deposit(event, user_id: int, amount: float, credits: int):
    """Real dynamic UPI QR via Razorpay — credits ONLY after Razorpay confirms the
    payment (webhook, or a live check when the user taps 'I Have Paid')."""
    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        await event.answer("❌ UPI / QR Auto is not set up yet. Please use Manual.", alert=True)
        return
    blocking = await _blocking_pending_deposit(user_id)
    if blocking:
        await _show_pending_deposit_block(event, blocking)
        return

    await event.edit(fancy("⏳ **Generating Live Payment QR...**"))
    dep = await deposits_col.insert_one({
        "user_id": user_id, "amount": amount, "credits": credits,
        "method": "razorpay_auto", "status": "pending", "created_at": datetime.utcnow(),
    })
    dep_id = str(dep.inserted_id)
    qr = await razorpay_create_qr(amount, dep_id, user_id)
    if not qr or not qr.get("id"):
        await deposits_col.update_one({"_id": dep.inserted_id}, {"$set": {"status": "failed"}})
        await event.edit(fancy("❌ **Could Not Generate QR.**\n\nPlease Try Manual Method Instead."),
                         buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")]])
        return

    content = qr.get("image_content")
    link = content if isinstance(content, str) and content.startswith("upi://") else None
    await deposits_col.update_one({"_id": dep.inserted_id}, {"$set": {
        "qr_code_id": qr["id"], "qr_image_url": qr.get("image_url"),
        "upi_link": link, "ui_msg_id": event.message_id,
    }})
    user_states[user_id] = {"deposit_id_obj": dep_id, "credits": credits, "amount": amount, "upi_link": link}

    msg = (
        fancy("⚡ **Scan & Pay Via UPI**\n\n")
        + fancy_line(f"• amount: ") + f"`₹{amount:.2f}`\n"
        + fancy_line(f"• credits: ") + f"`+{credits}`\n\n"
        + fancy_line(
            "scan the qr code with any upi app (gpay, phonepe, paytm, bhim).\n"
            "funds are credited automatically the moment payment is received — no need to tap anything.\n\n"
            "💫 valid for 30 minutes."
        )
    )
    await event.edit(msg, buttons=await _deposit_buttons(user_id, link))
    image_url = qr.get("image_url")
    if image_url:
        try:
            _track_temp(user_id, await event.respond(file=image_url))
        except Exception as e:
            log.warning(f"[Razorpay] could not send QR image: {e}")
            await event.edit(msg + f"\n\n{qr.get('short_url', '')}", buttons=await _deposit_buttons(user_id, link))

async def _start_manual_deposit(event, user_id: int, amount: float, credits: int):
    """UPI QR + screenshot + admin approval (no auto-credit possible here)."""
    upi_id = await get_setting("upi_id")
    upi_name = await get_setting("upi_name", "Prime Vault")
    if not upi_id:
        await event.answer("❌ UPI is not configured.", alert=True)
        return
    blocking = await _blocking_pending_deposit(user_id)
    if blocking:
        await _show_pending_deposit_block(event, blocking)
        return

    txn_id = f"DEP{datetime.utcnow().strftime('%y%m%d%H%M')}{uuid.uuid4().hex[:4].upper()}"
    link = (f"upi://pay?pa={upi_id}&pn={_urlquote(str(upi_name))}"
            f"&am={amount:.2f}&cu=INR&tn={txn_id}")
    dep = await deposits_col.insert_one({
        "user_id": user_id, "amount": amount, "credits": credits,
        "method": "manual", "txn_id": txn_id, "status": "pending",
        "created_at": datetime.utcnow(),
    })
    user_states[user_id] = {
        "state": "await_deposit_screenshot",
        "credits": credits, "amount": amount, "txn_id": txn_id,
        "upi_link": link, "ui_msg_id": event.message_id,
        "deposit_id_obj": str(dep.inserted_id),
    }
    msg = (
        fancy("📸 **UPI Manual Deposit**\n\n")
        + fancy_line("• amount: ") + f"`₹{amount:.2f}`\n"
        + fancy_line("• credits: ") + f"`+{credits}`\n"
        + fancy_line("• upi id: ") + f"`{upi_id}`\n"
        + fancy_line("• txn ref: ") + f"`{txn_id}`\n\n"
        + fancy_line(
            "scan the qr code with any upi app (gpay, phonepe, paytm, bhim) and pay the exact amount.\n"
            "then send the payment screenshot here — an admin will verify and approve it.\n\n"
            "💫 valid for 30 minutes."
        )
    )
    await event.edit(msg, buttons=[
        [color_btn("❌ ᴄᴀɴᴄᴇʟ", "cancel_payment", "danger")],
        [color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")],
    ])
    try:
        qr = _make_upi_qr(upi_id, amount, upi_name)
    except Exception as e:
        log.error(f"[manual deposit] QR generation crashed: {e}")
        qr = None
    if qr:
        try:
            qr_file = io.BytesIO(qr)
            qr_file.name = "upi_qr.png"
            _track_temp(user_id, await event.respond(file=qr_file))
        except Exception as e:
            log.error(f"[manual deposit] could not send QR image: {e}")
            qr = None
    if not qr:
        _track_temp(user_id, await event.respond(
            fancy(f"⚠️ **Couldn't Render The QR Image.**\n\nPay Manually Using UPI ID `{upi_id}` For `₹{amount:.2f}`, Then Send The Screenshot.")
        ))


# ─── 15. FANCY TYPOGRAPHY ───────────────────────────────────────
_SMALL_CAPS = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ',
    'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ',
    'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ',
    'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ',
    'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ',
    'z': 'ᴢ'
}
def fancy(text: str) -> str:
    """Plain, normal text — no small-caps styling. Kept as a pass-through so the
    many call sites across the bot don't need to change; headings stay readable
    via ordinary **bold** markdown already in the source strings."""
    return text

def fancy_caps(text: str) -> str:
    """Plain, normal text (see `fancy`) — used for formal copy like Terms & Conditions."""
    return text

# A short list of acronyms/terms that must stay fully capitalized even though the
# surrounding body copy is written in lowercase (sentence-case) source strings.
_KEEP_CAPS = {
    "otp": "OTP", "2fa": "2FA", "upi": "UPI", "qr": "QR", "vnh": "VNH",
    "id": "ID", "cr": "Cr", "gpay": "GPay", "phonepe": "PhonePe",
    "paytm": "Paytm", "bhim": "BHIM", "api": "API",
}

def _fix_acronyms(line: str) -> str:
    return re.sub(
        r"\b([A-Za-z0-9]+)\b",
        lambda m: _KEEP_CAPS.get(m.group(0).lower(), m.group(0)),
        line,
    )

def fancy_line(text: str) -> str:
    """Normal, readable text: only the first letter of each line is capitalized
    (sentence case), with known acronyms (OTP, UPI, 2FA, ...) kept fully capitalized."""
    out_lines = []
    for line in text.split("\n"):
        line = _fix_acronyms(line)
        m = re.search(r'[A-Za-z]', line)
        if not m:
            out_lines.append(line)
            continue
        idx = m.start()
        out_lines.append(line[:idx] + line[idx].upper() + line[idx+1:])
    return "\n".join(out_lines)

_BUTTON_NORMAL = {value: key.upper() for key, value in _SMALL_CAPS.items()}
_BUTTON_NORMAL['s'] = 'S'   # older hard-coded strings use a plain 's' inside small-caps words
_SC_MARKERS = {value for key, value in _SMALL_CAPS.items() if value != key}
def button_label(text: str) -> str:
    """Buttons: words written in small caps become full-size CAPS; ordinary
    words (admin-typed category/country names etc.) are left exactly as typed."""
    out = []
    for word in re.split(r'( )', text):
        if any(ch in _SC_MARKERS for ch in word):
            out.append(''.join(_BUTTON_NORMAL.get(ch, ch) for ch in word))
        else:
            out.append(word)
    return ''.join(out)

# ─── 16. KEYBOARD BUILDERS ──────────────────────────────────────
# FIXED: Use Button.inline with style argument (supports colored buttons)
def color_btn(text, data, style="default"):
    if style == "default":
        style = None
    return Button.inline(button_label(text), data, style=style)

async def main_menu_buttons(user_id: int) -> list:
    rows = []
    rows.append([color_btn("🌐 " + fancy("buy accounts"), "store", "default")])

    if await get_setting("whatsapp_enabled", False):
        rows.append([color_btn("💬 ʙᴜʏ ᴡʜᴀᴛsᴀᴘᴘ", "whatsapp", "primary")])

    rows.append([color_btn("💳 ᴀᴅᴅ ᴄʀᴇᴅɪᴛs", "deposit", "success")])

    rows.append([
        color_btn("💼 ᴡᴀʟʟᴇᴛ", "balance", "primary"),
        color_btn("📋 ᴍʏ ᴏʀᴅᴇʀs", "orders", "primary"),
    ])
    rows.append([color_btn("👤 ᴘʀᴏꜰɪʟᴇ", "profile", "primary")])
    rows.append([
        color_btn("🎫 sᴜᴘᴘᴏʀᴛ", "help", "success"),
        color_btn("📜 ᴛᴇʀᴍs ᴏꜰ sᴇʀᴠɪᴄᴇ", "show_terms", "success"),
    ])

    if await is_admin(user_id):
        rows.append([color_btn("⚙️ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", "admin", "danger")])

    return rows

async def _per_row() -> int:
    try:
        return max(1, min(4, int(await get_setting("buttons_per_row", 2))))
    except (TypeError, ValueError):
        return 2

async def _category_buttons(categories: List[dict], back_data: str = "store", server: int = 1) -> list:
    rows = []
    per_row = await _per_row()
    stock_filter = (
        {"$or": [{"server": 1}, {"server": {"$exists": False}}]} if server == 1
        else {"server": server}
    )
    with_stock = []
    for cat in categories:
        name = str(cat.get("name", "Category")).strip()
        has_stock = await accounts_col.count_documents(
            {"category": name, "status": "available", **stock_filter}, limit=1
        ) > 0
        with_stock.append((name, has_stock))
    # Categories with stock are listed first, so buyers always see what's actually sellable.
    with_stock.sort(key=lambda t: not t[1])

    row = []
    for name, has_stock in with_stock:
        label = f"✅ {name}" if has_stock else f"❌ {name}"
        data = f"category:{name}" if has_stock else "category_empty"
        row.append(color_btn(label, data, "default"))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([color_btn("◀️ ʙᴀᴄᴋ", back_data, "danger")])
    rows.append([color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "primary")])
    return rows

def _inventory_buttons(countries: List[dict], page: int = 0, per_page: int = 8, per_row: int = 2) -> list:
    total = len(countries)
    start = page * per_page
    end   = min(start + per_page, total)
    rows  = []
    for c in countries[start:end]:
        stock = c["stock"]
        price = c["price"]
        name = c["name"]
        if len(name) > 14:                       # keeps the button from overflowing on
            name = name[:13].rstrip() + "…"       # narrow screens (Telegram can't scroll/marquee button text)
        rows.append([
            color_btn(f"{c['flag']} {name} ({stock})", f"buy:{c['code']}", "default"),
            color_btn(f"{price} Cr", f"buy:{c['code']}", "default"),
            color_btn("Buy ✅", f"buy:{c['code']}", "success"),
        ])

    nav = []
    if page > 0:
        nav.append(color_btn("◀️", f"store_page:{page-1}", "default"))
    nav.append(color_btn(f"{page+1}/{(total-1)//per_page + 1}", "store_noop", "default"))
    if end < total:
        nav.append(color_btn("▶️", f"store_page:{page+1}", "default"))
    if nav:
        rows.append(nav)
    rows.append([color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")])
    return rows

def _admin_menu_buttons(is_owner_flag: bool) -> list:
    rows = [
        [color_btn("📊 sᴛᴀᴛs", "astats", "primary")],
        [color_btn("📦 ᴜᴘʟᴏᴀᴅ sᴇssɪᴏɴs", "upload_sessions", "primary"),
         color_btn("📋 sᴇssɪᴏɴ ᴏᴠᴇʀᴠɪᴇᴡ", "manage_sessions", "primary")],
        [color_btn("💳 ᴘᴇɴᴅɪɴɢ ᴅᴇᴘᴏsɪᴛs", "pending_deposits", "primary")],
        [color_btn("📢 ʙʀᴏᴀᴅᴄᴀsᴛ", "broadcast", "primary")],
        [color_btn("⚙️ sᴇᴛᴛɪɴɢs", "asettings", "primary"),
         color_btn("🌍 ᴄᴏᴜɴᴛʀɪᴇs", "acountries", "primary")],
        [color_btn("🗂️ ᴄᴀᴛᴇɢᴏʀɪᴇs", "acategories", "primary")],
        [color_btn("👤 ᴜsᴇʀs", "ausers", "primary")],
    ]
    if is_owner_flag:
        rows.append([color_btn("🔑 ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs", "manage_admins", "primary")])
    rows.append([color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")])
    return rows

DEFAULT_TERMS_TEXT = (
    "⚠️ **Terms & Conditions**\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "Please read and accept our terms to use this bot:\n\n"
    "📍 **Account policy**\n"
    "• These accounts are for testing / educational purposes.\n"
    "• We are **not** responsible for any ban or freeze after login.\n"
    "• Use Telegram X or an official app for best stability.\n\n"
    "💰 **Refund policy**\n"
    "• **No refunds** under any circumstances, except 'No OTP received'.\n"
    "• All sales are final — buy at your own risk.\n\n"
    "🚫 **Misuse:** any illegal activity will result in a ban.\n\n"
    "__By tapping 'Accept', you agree to all the terms above.__"
)

async def _welcome_text(user_id: int, first_name: str) -> str:
    """HTML (not the bot's default markdown) so the closing line can use a real
    Telegram quote block — _send_main_welcome sends this with parse_mode='html'."""
    first_name = _html.escape(re.sub(r"[*_`\[\]]", "", first_name or "User") or "User")
    user = await users_col.find_one({"user_id": user_id}) or {}
    bal = float(user.get("balance", 0))
    bot_name = _html.escape(await get_setting("bot_name", "Prime Vault"))
    header = (
        f"❄️ <b>Welcome to {bot_name}</b> 🔥\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<b>Welcome, {first_name}</b>\n<b>Balance : {bal:.2f} Cr</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )
    body = (
        "✨ <b>What you can do:</b>\n"
        "• Buy Telegram accounts with instant OTP & 2FA details\n"
        "• Add credits via UPI / QR\n"
        "• Choose your server, category, country and price\n"
        "• Check stock and price before you buy\n\n"
        "<blockquote>👇 Choose an option below to get started.</blockquote>"
    )
    return header + body

async def _send_main_welcome(event, user_id: int):
    sender = await event.get_sender()
    welcome_msg = await _welcome_text(user_id, getattr(sender, "first_name", None))
    photo_id = await get_setting("welcome_photo")
    if photo_id:
        try:
            await event.respond(file=photo_id, message=welcome_msg, buttons=await main_menu_buttons(user_id), parse_mode="html")
            return
        except Exception:
            pass
    await event.respond(welcome_msg, buttons=await main_menu_buttons(user_id), parse_mode="html")

# ─── 17. /start COMMAND ─────────────────────────────────────────
@bot.on(events.NewMessage(pattern="/start"))
async def cmd_start(event):
    user_id     = event.sender_id
    args        = event.message.text.split()
    referrer_id = None
    if len(args) > 1:
        val = args[1].lstrip("ref").lstrip("_")
        try:
            referrer_id = int(val)
        except ValueError:
            ref_user = await users_col.find_one({"referral_code": val})
            if ref_user:
                referrer_id = ref_user["user_id"]

    user = await get_or_create_user(user_id, referrer_id)
    if user.get("is_banned"):
        await event.respond("🚫 You are banned from this bot.")
        return
    if not await is_user_member(user_id):
        await send_join_message(event)
        return

    if not user.get("accepted_terms"):
        terms_text = await get_setting("terms_text") or DEFAULT_TERMS_TEXT
        await event.respond(
            fancy_caps(terms_text),
            buttons=[
                [color_btn("✅ ᴀᴄᴄᴇᴘᴛ", "terms_accept", "success"),
                 color_btn("❌ ʀᴇᴊᴇᴄᴛ", "terms_decline", "danger")],
            ]
        )
        return

    await _send_main_welcome(event, user_id)



# ─── 17b. BROWSE / UPLOAD HELPERS ──────────────────────────────
DEFAULT_SERVER_LABELS = {
    "1": {"tag": "Private Stock", "bullets": [
        "Cheapest option",
        "Directly managed private stock",
        "Limited stock may vary by country",
    ]},
    "2": {"tag": "External Server", "bullets": [
        "Accounts are sourced from a third-party server",
        "More countries and stock may be available",
        "Price depends on live server rates",
        "Third-party terms apply",
    ]},
}

def _vnh_fetch_value(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None

async def _send_vnh_country_list(event, user_id: int, page: int, search: str = None):
    countries = await vnh_server.available_countries()
    if search:
        s_ = search.strip().lower()
        countries = [c for c in countries if s_ in c["name"].lower() or s_ in c["code"].lower()]
    if not countries:
        await event.edit(
            fancy("😔 **No Countries Found.**\n\n")
            + fancy_line("the supplier has no stock right now, or no country matched your search."),
            buttons=[[color_btn("◀️ ʙᴀᴄᴋ ᴛᴏ sᴇʀᴠᴇʀs", "store", "default")]]
        )
        return
    per_page = 8
    pages = (len(countries) - 1) // per_page + 1
    page = max(0, min(page, pages - 1))
    chunk = countries[page * per_page: page * per_page + per_page]
    rows = []
    for c in chunk:
        qty = c.get("qty", 0) or 0
        label = f"{c['name']} • {qty} ɪɴ sᴛᴏᴄᴋ"
        if c.get("price") is not None:
            try:
                calc = await vnh_server.calculate_price(float(c["price"]))
                label = f"{c['name']} • ₹{calc['retail_inr']:.0f} • {qty} ɪɴ sᴛᴏᴄᴋ"
            except Exception:
                pass
        rows.append([color_btn(label, f"vnh_country:{c['code']}", "primary")])
    nav = []
    if page > 0:
        nav.append(color_btn("◀️", f"vnh_page:{page-1}", "default"))
    nav.append(color_btn(f"{page+1}/{pages}", "store_noop", "default"))
    if page + 1 < pages:
        nav.append(color_btn("▶️", f"vnh_page:{page+1}", "default"))
    rows.append(nav)
    rows.append([color_btn("🔍 sᴇᴀʀᴄʜ ᴄᴏᴜɴᴛʀʏ", "vnh_search", "success")])
    rows.append([color_btn("◀️ ʙᴀᴄᴋ ᴛᴏ sᴇʀᴠᴇʀs", "store", "default")])
    await event.edit(
        fancy("🛍️ **Available Telegram Services**\n\n")
        + fancy_line(f"choose any country to continue.\npage {page+1}/{pages}"),
        buttons=rows
    )

async def _enter_server(event, user_id: int, server_num: int):
    """Shared by tapping a server button and the single-active-server auto-skip."""
    if server_num == int(await get_setting("vnh_server_number", 2)):
        if not vnh_server.configured:
            await event.edit(
                fancy("⚠️ **This Server Isn't Connected Yet.**\n\n")
                + fancy_line("the admin needs to add a vnh api key before this server can be used. please try server 1."),
                buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "store", "default")]]
            )
            return
        await _send_vnh_country_list(event, user_id, 0)
        return
    cats = await get_categories()
    await event.edit(
        fancy(f"📂 **Select Account Category** • Server {server_num}\n\n")
        + fancy_line(
            "choose the category that best fits your need.\n"
            "• stock & price are shown before purchase\n"
            "• each category opens its available countries\n\n"
            "👇 tap a category below:"
        ),
        buttons=await _category_buttons(cats, back_data="store", server=server_num)
    )

async def _server_active(server: int) -> bool:
    active = await get_setting("server_active", None)
    active = active if isinstance(active, dict) else {}
    if str(server) in active:
        return bool(active[str(server)])
    return server == 1   # Server 1 is on by default; extra servers start off until wired up

async def _server_label(server: int) -> dict:
    labels = await get_setting("server_labels", None)
    labels = labels if isinstance(labels, dict) else {}
    custom = labels.get(str(server))
    if isinstance(custom, dict) and custom.get("bullets"):
        return custom
    default = DEFAULT_SERVER_LABELS.get(str(server))
    if default:
        return default
    return {"tag": "Extra Stock", "bullets": ["Instant delivery"]}

async def _server_year_cfg(server: int) -> dict:
    all_cfg = await get_setting("server_year_config", None)
    all_cfg = all_cfg if isinstance(all_cfg, dict) else {}
    cfg = all_cfg.get(str(server)) or {}
    years = cfg.get("years")
    if not isinstance(years, list) or not years:
        years = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]
    return {"enabled": bool(cfg.get("enabled", False)), "years": [str(y) for y in years]}

async def _set_server_year_cfg(server: int, **kwargs) -> None:
    all_cfg = await get_setting("server_year_config", None)
    all_cfg = all_cfg if isinstance(all_cfg, dict) else {}
    cur = all_cfg.get(str(server)) or {}
    cur.update(kwargs)
    all_cfg[str(server)] = cur
    await set_setting("server_year_config", all_cfg)

async def _send_country_list(event, user_id: int, page: int = 0):
    st = user_states.get(user_id, {})
    cat_name = st.get("category")
    server = st.get("server", 1)
    year = st.get("year")
    countries = await get_active_countries(cat_name, server=server, year=year)
    back = f"category:{cat_name}" if (year and cat_name) else f"server:{server}"
    if not countries:
        await event.edit(
            fancy(
                f"😔 **No Accounts Available In '{cat_name}'{' (' + str(year) + ')' if year else ''}.**\n\n"
                "• This Option Is Currently Out Of Stock\n"
                "• Please Choose Another Option To Continue"
            ),
            buttons=[[color_btn("◀️ ʙᴀᴄᴋ", back, "default")]]
        )
        return
    title = f"{cat_name} • {year}" if year else f"{cat_name}"
    await event.edit(
        fancy(
            f"📌 **{title}**\n\n"
            "🌍 Choose A Country To View Available Stock And Price.\n"
            "• Each Option Shows Stock And Credit Price\n\n"
            "👇 Tap An Option To Continue:"
        ),
        buttons=_inventory_buttons(countries, page, per_row=await _per_row())
    )

async def _begin_upload(event, user_id: int, srv_n, cc: str, cat_name: str, year=None):
    c = await countries_col.find_one({"code": cc})
    if not c:
        await event.answer("❌ Country not found.", alert=True)
        return
    user_states[user_id] = {
        "state": "waiting_zip",
        "country_code": cc,
        "country_name": c["name"],
        "price": float(c["price"]),
        "category": cat_name,
        "server": int(srv_n),
        "year": str(year) if year else None,
        "twofa_password": "",
    }
    default_2fa = await get_setting("default_2fa", "")
    hint = f"\n\n💡 Default 2FA: `{default_2fa}`" if default_2fa else ""
    yr = f" • {year}" if year else ""
    await event.edit(
        fancy(
            f"📦 **Upload Sessions — {c['flag']} {c['name']} ({cat_name}{yr}) • Server {srv_n}**\n\n"
            "Step 1 (Optional): Send 2FA Password As Text.\n"
            f"If Not Sent, Default 2FA Will Be Used.{hint}\n"
            "Step 2: Send The **.zip** Or **.session** File."
        ),
        buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "admin", "danger")]],
    )

# ─── 18. CALLBACK ROUTER ────────────────────────────────────────
@bot.on(events.CallbackQuery())
async def callback_router(event, data_override=None):
    data    = data_override or (event.data.decode() if isinstance(event.data, bytes) else event.data)
    user_id = event.sender_id

    user = await users_col.find_one({"user_id": user_id})
    if user and user.get("is_banned"):
        await event.answer("🚫 You are banned.", alert=True)
        return

    if data != "check_join" and not await is_user_member(user_id):
        await event.answer("❌ Join required channels first!", alert=True)
        return

    # Any helper messages (QR image, notes) from the previous screen are no longer needed.
    if data not in _KEEP_TEMP_ON:
        await _cleanup_temp(user_id)

    # ── MAIN MENU ────────────────────────────────────────────────
    if data == "main_menu":
        user_states.pop(user_id, None)
        sender = await event.get_sender()
        await event.edit(
            await _welcome_text(user_id, getattr(sender, "first_name", None)),
            buttons=await main_menu_buttons(user_id),
            parse_mode="html",
        )

    # ── CHECK JOIN ───────────────────────────────────────────────
    elif data == "check_join":
        if await is_user_member(user_id):
            await event.answer("✅ Verified!")
            await cmd_start(event)
        else:
            await event.answer("❌ You haven't joined yet!", alert=True)

    # ── TERMS & CONDITIONS ───────────────────────────────────────
    elif data == "show_terms":
        terms_text = await get_setting("terms_text") or DEFAULT_TERMS_TEXT
        await event.edit(
            fancy_caps(terms_text),
            buttons=[[color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]]
        )

    elif data == "terms_accept":
        await users_col.update_one({"user_id": user_id}, {"$set": {"accepted_terms": True, "accepted_terms_at": datetime.utcnow()}})
        await event.answer("✅ Terms accepted!")
        try:
            await event.delete()          # remove the old T&C message
        except Exception:
            pass
        await _send_main_welcome(event, user_id)

    elif data == "terms_decline":
        await event.edit(
            fancy_caps("❌ You Must Accept The Terms To Use This Bot.\n\nSend /start To Try Again."),
        )

    # ── STORE (server select) ───────────────────────────────────
    elif data == "store":
        user_states.pop(user_id, None)
        server_count = int(await get_setting("server_count", 2))
        active_list = [i for i in range(1, server_count + 1) if await _server_active(i)]
        if len(active_list) == 1 and await get_setting("skip_server_step_if_one", True):
            user_states[user_id] = {"server": active_list[0]}
            await _enter_server(event, user_id, active_list[0])
            return
        per_row = await _per_row()
        rows, row, blocks = [], [], []
        for i in range(1, server_count + 1):
            active = await _server_active(i)
            info = await _server_label(i)
            dot = "🟢" if active else "🔴"
            style = "success" if active else "danger"
            row.append(color_btn(f"{fancy('server')} {i}", f"server:{i}", style))
            if len(row) == per_row:
                rows.append(row)
                row = []
            bullets = "\n".join(f"• {b}" for b in info.get("bullets", []))
            tag = info.get("tag", "")
            status = "" if active else "  (Not Active Yet)"
            blocks.append(fancy_line(f"{dot} Server {i} — {tag}{status}\n{bullets}"))
        if row:
            rows.append(row)
        rows.append([color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")])
        await event.edit(
            fancy("🛒 **Buy Accounts**\n\n") + fancy_line("choose your preferred server:\n\n")
            + "\n\n".join(blocks)
            + "\n\n" + fancy_line("select a server below to continue."),
            buttons=rows
        )

    elif data.startswith("server:"):
        server_num = int(data.split(":", 1)[1])
        if not await _server_active(server_num):
            await event.answer(f"⚠️ Server {server_num} isn't activated right now — please try Server 1.", alert=True)
            return
        user_states[user_id] = {"server": server_num}
        await _enter_server(event, user_id, server_num)

    elif data.startswith("vnh_page:"):
        page = int(data.split(":", 1)[1])
        await _send_vnh_country_list(event, user_id, page, user_states.get(user_id, {}).get("vnh_search"))

    elif data == "vnh_search":
        user_states[user_id] = {"state": "vnh_search", "server": user_states.get(user_id, {}).get("server", 2)}
        await event.edit(fancy("🔍 **Send The Country Name To Search:**"),
                          buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "store", "danger")]])

    elif data.startswith("vnh_country:"):
        code = data.split(":", 1)[1]
        await event.answer("⏳ Fetching live price…")
        result = await vnh_server.get_country_live_price(code)
        if not result.get("success"):
            await event.answer(f"❌ {result.get('message', 'Could not fetch live price.')}", alert=True)
            return
        if not user:
            user = await get_or_create_user(user_id)
        price = result["retail_inr"]
        bal = float(user.get("balance", 0))
        user_states[user_id] = {
            "server": int(await get_setting("vnh_server_number", 2)),
            "vnh_code": code, "vnh_price": price,
        }
        if bal < price:
            await event.edit(
                fancy("⚠️ **Insufficient Funds**\n\n")
                + fancy_line(f"price: ") + f"`{price:.2f} ᴄʀ`\n"
                + fancy_line(f"your balance: ") + f"`{bal:.2f} ᴄʀ`\n\n"
                + fancy_line("please add funds to continue with this purchase."),
                buttons=[
                    [color_btn("💳 ᴀᴅᴅ ꜰᴜɴᴅs", "deposit", "success")],
                    [color_btn("◀️ ʙᴀᴄᴋ", "store", "default")],
                ]
            )
            return
        await event.edit(
            fancy("⚡ **Confirm Purchase**\n\n")
            + fancy_line(f"country code: ") + f"`{code}`\n"
            + fancy_line(f"price: ") + f"`{price:.2f} ᴄʀ`\n"
            + fancy_line(f"your balance: ") + f"`{bal:.2f} ᴄʀ`",
            buttons=[
                [color_btn(f"✅ Buy — {price:.2f} ᴄʀ", f"vnh_buy:{code}", "success")],
                [color_btn("❌ ᴄᴀɴᴄᴇʟ", "store", "danger")],
            ]
        )

    elif data.startswith("vnh_buy:"):
        code = data.split(":", 1)[1]
        st = user_states.get(user_id, {})
        price = st.get("vnh_price")
        if not price or st.get("vnh_code") != code:
            await event.answer("❌ Session expired — select the country again.", alert=True)
            return
        if not user:
            user = await get_or_create_user(user_id)
        bal = float(user.get("balance", 0))
        if bal < price:
            await event.answer("❌ Insufficient balance!", alert=True)
            return

        result = await users_col.update_one(
            {"user_id": user_id, "balance": {"$gte": price}},
            {"$inc": {"balance": -price}}
        )
        if result.modified_count == 0:
            await event.answer("❌ Insufficient balance!", alert=True)
            return

        order_result = await vnh_server.place_order(code)
        if not vnh_server.ok(order_result):
            # Never show raw supplier/provider errors to the customer.
            log.error(f"[VNH] order failed user={user_id} code={code} response={order_result!r}")
            await users_col.update_one({"user_id": user_id}, {"$inc": {"balance": price}})
            await event.edit(
                fancy(f"❌ **Something Went Wrong. Please Contact Admin.**\n\n`{price:.2f} Cr` Has Been Returned To Your Balance."),
                buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "store", "default")]]
            )
            await log_event(f"⚠️ **VNH order failed**\n• User: `{user_id}` • Code: `{code}`\n• Response: `{str(order_result)[:200]}`")
            return

        odata = order_result.get("data", order_result) if isinstance(order_result, dict) else {}
        number = _vnh_fetch_value(odata, "number", "phone", "phone_number")
        if not number:
            await users_col.update_one({"user_id": user_id}, {"$inc": {"balance": price}})
            await event.edit(
                fancy("❌ **Supplier Didn't Return A Number.** You Have Been Refunded."),
                buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "store", "default")]]
            )
            return

        await orders_col.insert_one({
            "user_id": user_id, "phone": number, "country_code": code,
            "amount": price, "source": "vnh", "status": "waiting_otp",
            "created_at": datetime.utcnow(),
        })
        user_states.pop(user_id, None)
        await event.edit(
            fancy("✅ **Purchase Successful!**\n\n")
            + fancy_line("number: ") + f"`{number}`\n\n"
            + fancy_line("tap below to fetch the otp — it may take a few seconds."),
            buttons=[
                [color_btn("📩 ɢᴇᴛ ᴏᴛᴘ", f"vnh_otp:{number}", "success")],
                [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")],
            ]
        )
        await log_event(f"🛒 **VNH Purchase**\n• User: `{user_id}`\n• {code} — `{price:.2f} Cr`\n• Number: `{number}`")

    elif data.startswith("vnh_otp:"):
        number = data.split(":", 1)[1]
        result = await vnh_server.get_code(number)
        if not vnh_server.ok(result):
            await event.answer("⏳ OTP not received yet — try again in a few seconds.", alert=True)
            return
        odata = result.get("data", result) if isinstance(result, dict) else {}
        otp = _vnh_fetch_value(odata, "code", "otp", "otp_code")
        pwd = _vnh_fetch_value(odata, "password", "2fa", "twofa")
        if not otp:
            await event.answer("⏳ OTP not received yet — try again in a few seconds.", alert=True)
            return
        msg = fancy("✅ **OTP Received!**\n\n") + fancy_line("code: ") + f"`{otp}`\n"
        if pwd:
            msg += fancy_line("2fa password: ") + f"`{pwd}`\n"
        await event.edit(msg, buttons=[
            [color_btn("🔄 ʀᴇꜰʀᴇsʜ", f"vnh_otp:{number}", "primary")],
            [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")],
        ])

    elif data == "category_empty":
        await event.answer("❌ Stock is not available for this category right now.", alert=True)

    elif data.startswith("category:"):
        cat_name = data.split(":", 1)[1]
        prev_server = user_states.get(user_id, {}).get("server", 1)
        user_states[user_id] = {"category": cat_name, "server": prev_server}
        year_cfg = await _server_year_cfg(prev_server)
        if year_cfg["enabled"]:
            per_row = await _per_row()
            rows, row = [], []
            for y in year_cfg["years"]:
                n = await accounts_col.count_documents({"category": cat_name, "year": y, "status": "available",
                                                        **({"$or": [{"server": 1}, {"server": {"$exists": False}}]} if prev_server == 1 else {"server": prev_server})})
                if n <= 0:
                    continue
                row.append(color_btn(f"{y} • {n}", f"year:{y}", "default"))
                if len(row) == per_row:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)
            rows.append([color_btn("◀️ ʙᴀᴄᴋ", f"server:{prev_server}", "default"),
                         color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")])
            await event.edit(
                fancy(
                    f"📅 **Select Year** • {cat_name}\n\n"
                    "✨ Choose The Account Year You Want.\n\n"
                    + ("👇 Tap A Year Below:" if len(rows) > 1 else "😔 No Stock Available Right Now.")
                ),
                buttons=rows
            )
            return
        await _send_country_list(event, user_id)

    elif data.startswith("year:"):
        st = user_states.get(user_id)
        if not st or "category" not in st:
            await event.answer("❌ Session expired. Start again.", alert=True)
            return
        st["year"] = data.split(":", 1)[1]
        await _send_country_list(event, user_id)

    elif data.startswith("store_page:"):
        page = int(data.split(":")[1])
        if not user_states.get(user_id, {}).get("category"):
            await event.answer("❌ Session expired. Start again.", alert=True)
            return
        await _send_country_list(event, user_id, page)

    elif data == "store_noop":
        await event.answer()

    # ── ITEM DETAIL ──────────────────────────────────────────────
    elif data.startswith("detail:"):
        code = data.split(":")[1]
        c = await countries_col.find_one({"code": code})
        if not c:
            await event.answer("❌ Country not found.", alert=True)
            return
        cat_name = user_states.get(user_id, {}).get("category")
        stock = await accounts_col.count_documents({
            "country_code": code,
            "status": "available",
            "category": cat_name
        })
        # Step 3: Credit system (cr)
        text = fancy(
            f"🌍 **{c['flag']} {c['name']}**\n\n"
            f"• Stock: `{stock}`\n"
            f"• Price: `{c['price']} Cr`\n"
            f"• Category: `{cat_name or 'General'}`\n\n"
            "Purchase using the buy button below."
        )
        await event.edit(text, buttons=[
            [color_btn("🟢 ʙᴜʏ ɴᴏᴡ", f"buy:{code}", "success"),
             color_btn("◀️ ʙᴀᴄᴋ", "store", "default")],
            [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]
        ])

    # ── BUY ──────────────────────────────────────────────────────
    elif data.startswith("buy:"):
        code = data.split(":")[1]
        server_num = user_states.get(user_id, {}).get("server", 1)
        if server_num != 1:
            await event.answer("⚠️ Yeh server abhi connect nahi hua — jald aa raha hai!", alert=True)
            return
        country = await countries_col.find_one({"code": code, "is_active": True})
        if not country:
            await event.answer("❌ Country unavailable.", alert=True)
            return
        cat_name = user_states.get(user_id, {}).get("category")
        stock = await accounts_col.count_documents({
            "country_code": code,
            "status": "available",
            "category": cat_name
        })
        if stock == 0:
            await event.answer("❌ Out of stock!", alert=True)
            return
        if not user:
            user = await get_or_create_user(user_id)
        bal = float(user.get("balance", 0))
        price = float(country["price"])  # Price in Credits (cr)
        
        # Step 4: Insufficient Funds
        if bal < price:
            needed = price - bal
            text = fancy(
                f"⚠️ **Insufficient funds**\n\n"
                f"Account: {country['flag']} {country['name']} (+••••••••)\n"
                f"Price: `{price} Cr`\n"
                f"Available funds: `{bal} Cr`\n"
                f"Short by: `{needed} Cr`\n\n"
                "Please add funds to proceed with this purchase."
            )
            await event.edit(text, buttons=[
                [color_btn("+1 ᴀᴅᴅ ꜰᴜɴᴅs", "deposit", "success")],
                [color_btn("◀️ ʙᴀᴄᴋ", "store", "default")],
                [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]
            ])
            return
        
        # Confirm purchase (Step 4: Big Buy Button)
        text = fancy(
            f"⚡ **Confirm purchase**\n\n"
            f"🌍 Country: {country['flag']} {country['name']}\n"
            f"💰 Price: `{price} Cr`\n"
            f"💎 Your balance: `{bal} Cr`"
        )
        await event.edit(text, buttons=[
            [color_btn(f"✅ ʙᴜʏ ᴛʜɪs ᴀᴄᴄᴏᴜɴᴛ · {price} ᴄʀ", f"confirm_buy:{code}", "success")],
            [color_btn("❌ ᴄᴀɴᴄᴇʟ", "store", "danger")],
            [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]
        ])

    # ── CONFIRM BUY ──────────────────────────────────────────────
    elif data.startswith("confirm_buy:"):
        country_code = data.split(":")[1]
        country = await countries_col.find_one({"code": country_code, "is_active": True})
        if not country:
            await event.answer("❌ Country no longer available.", alert=True)
            return
        if not user:
            user = await get_or_create_user(user_id)
        price = float(country["price"])  # Credits
        bal = float(user.get("balance", 0))
        if bal < price:
            await event.answer("❌ Insufficient balance!", alert=True)
            return
        cat_name = user_states.get(user_id, {}).get("category")

        session_doc = await accounts_col.find_one_and_update(
            {"country_code": country_code, "status": "available", "category": cat_name},
            {"$set": {
                "status":   "sold",
                "buyer_id": user_id,
                "sold_at":  datetime.utcnow(),
            }},
        )
        if not session_doc:
            await event.answer("❌ Out of stock — someone just bought the last one!", alert=True)
            return

        await users_col.update_one({"user_id": user_id}, {"$inc": {"balance": -price}})

        if user.get("referred_by"):
            pct = float(await get_setting("referral_percent", 0))
            bonus = round(price * pct / 100, 2)
            if bonus > 0:
                await users_col.update_one(
                    {"user_id": user["referred_by"]},
                    {"$inc": {"balance": bonus, "referral_earnings": bonus,
                              "withdrawable": bonus}},
                )
                try:
                    await bot.send_message(
                        user["referred_by"],
                        fancy(f"💸 **Referral earning!**\n+₹{bonus:.2f} from a purchase."),
                    )
                except Exception:
                    pass

        phone = session_doc["phone"]
        twofa = session_doc.get("twofa_password", "")
        await orders_col.insert_one({
            "user_id":      user_id,
            "phone":        phone,
            "country":      country["name"],
            "country_code": country_code,
            "country_flag": country.get("flag", ""),
            "amount":       price,
            "twofa":        twofa,
            "category":     cat_name,
            "status":       "waiting_otp",
            "created_at":   datetime.utcnow(),
        })
        pending_otp_requests[(user_id, phone)] = True
        await log_event(f"🛒 **ᴘᴜʀᴄʜᴀsᴇ**\n• ᴜsᴇʀ: `{user_id}`\n• {country.get('flag','')} {country['name']} — `{price} ᴄʀ`\n• ᴘʜᴏɴᴇ: `{phone}`")

        # Step 4/7: Purchase success + OTP Request
        msg = fancy(
            f"✅ **ᴘᴜʀᴄʜᴀsᴇ sᴜᴄᴄᴇssꜰᴜʟ!**\n\n"
            f"🌍 ᴄᴏᴜɴᴛʀʏ: {country.get('flag','')} {country['name']}\n"
            f"📞 ᴘʜᴏɴᴇ: `{phone}`\n"
            f"📂 ᴄᴀᴛᴇɢᴏʀʏ: `{cat_name}`\n"
            f"🔐 2FA: `{twofa}`\n\n"
            "⏳ ᴘʟᴇᴀsᴇ ᴛᴀᴘ **ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ** ᴛᴏ ɢᴇᴛ ʏᴏᴜʀ ᴄᴏᴅᴇ."
        )
        await event.edit(msg, buttons=[
            [color_btn("📩 ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ", f"resend_{phone}", "primary")],
            [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]
        ])

    # ── REQUEST OTP ─────────────────────────────────────────────
    elif data.startswith("resend_"):
        phone = data[7:]
        await event.answer("⏳ Requesting OTP…", alert=False)
        pending_otp_requests[(user_id, phone)] = True   # re-arm — otherwise the next OTP is silently dropped
        success = await acc_mgr.request_otp(phone)
        if not success:
            pending_otp_requests.pop((user_id, phone), None)
        if success:
            _track_temp(user_id, await event.respond(f"📤 OTP request sent for `{phone}`."))
        else:
            _track_temp(user_id, await event.respond(f"❌ Could not trigger OTP for `{phone}`."))

    # ── LOGOUT ──────────────────────────────────────────────────
    elif data.startswith("logout_"):
        phone = data[7:]
        await acc_mgr.logout_client(phone)
        await accounts_col.find_one_and_update(
            {"phone": phone, "status": "sold"},
            {"$set": {"status": "logged_out", "logged_out_at": datetime.utcnow()}},
            sort=[("sold_at", -1)],
        )
        await event.answer("✅ Logged out.", alert=False)
        await event.edit(
            fancy(f"🔓 **ʟᴏɢɢᴇᴅ ᴏᴜᴛ**\n\n`{phone}` ʜᴀs ʙᴇᴇɴ ᴅɪsᴄᴏɴɴᴇᴄᴛᴇᴅ."),
            buttons=[[color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]],
        )

    # ── MY ORDERS ──────────────────────────────────────────────
    elif data == "orders":
        docs = await orders_col.find(
            {"user_id": user_id}
        ).sort("created_at", -1).limit(8).to_list(8)
        if not docs:
            await event.edit(
                fancy("📋 **ᴍʏ ᴏʀᴅᴇʀs**\n\n_ɴᴏ ᴏʀᴅᴇʀs ʏᴇᴛ._"),
                buttons=[[color_btn("🛒 ʙᴜʏ ɴᴏᴡ", "store", "success"),
                          color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]],
            )
            return
        _STATUS = {
            "waiting_otp": "⏳ ᴡᴀɪᴛɪɴɢ ᴏᴛᴘ",
            "completed":   "✅ ᴄᴏᴍᴘʟᴇᴛᴇᴅ",
            "cancelled":   "❌ ᴄᴀɴᴄᴇʟʟᴇᴅ",
            "logged_out":  "🔓 ʟᴏɢɢᴇᴅ ᴏᴜᴛ",
        }
        lines = [fancy("📋 **ᴍʏ ᴏʀᴅᴇʀs — ʟᴀsᴛ 8**\n")]
        for i, o in enumerate(docs, 1):
            st = _STATUS.get(o.get("status", ""), o.get("status", "").title())
            line = (
                f"**{i}.** {o.get('country_flag','')} **{o.get('country','?')}**  —  {st}\n"
                f"📱 `{o.get('phone','?')}`  •  {o.get('amount',0):.0f} ᴄʀ"
            )
            if o.get("twofa"):
                line += f"\n🔐 2FA: `{o['twofa']}`"
            lines.append(line)
        kb = [[color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]]
        if docs[0].get("status") == "waiting_otp":
            kb.insert(0, [color_btn("📩 ʀᴇ-ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ", f"resend_{docs[0]['phone']}", "primary")])
        await event.edit("\n\n".join(lines), buttons=kb)

    # ── HISTORY ──────────────────────────────────────────────────
    elif data == "history":
        orders = await orders_col.find(
            {"user_id": user_id}).sort("created_at", -1).limit(6).to_list(6)
        deps   = await deposits_col.find(
            {"user_id": user_id}).sort("created_at", -1).limit(5).to_list(5)
        spent   = sum(float(o.get("amount", 0)) for o in orders
                      if o.get("status") != "cancelled")
        dep_tot = sum(float(d.get("amount", 0)) for d in deps
                      if d.get("status") == "approved")
        lines = [
            fancy("📋 **ʜɪsᴛᴏʀʏ**\n"),
            f"💸 ᴛᴏᴛᴀʟ sᴘᴇɴᴛ:     `{spent:.2f} ᴄʀ`",
            f"💰 ᴛᴏᴛᴀʟ ᴅᴇᴘᴏsɪᴛᴇᴅ: `{dep_tot:.2f} ᴄʀ`",
        ]
        if orders:
            lines.append("\n🛒 **ʀᴇᴄᴇɴᴛ ᴘᴜʀᴄʜᴀsᴇs:**")
            for o in orders:
                lines.append(
                    f"• {o.get('country_flag','')} {o.get('country','?')}"
                    f" — `{o.get('amount',0):.0f} ᴄʀ` — `{o.get('phone','?')}`"
                )
        if deps:
            lines.append("\n💰 **ʀᴇᴄᴇɴᴛ ᴅᴇᴘᴏsɪᴛs:**")
            _DE = {"approved": "✅", "pending": "⏳", "rejected": "❌"}
            for d in deps:
                lines.append(
                    f"{_DE.get(d.get('status',''),'•')} "
                    f"{d.get('amount',0):.0f} ᴄʀ — {d.get('status','').title()}"
                )
        await event.edit(
            "\n".join(lines),
            buttons=[[color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]],
        )

    # ─── DEPOSIT – PACKAGES ──────────────────────────────────────
    elif data == "deposit":
        pkgs = []
        async for p in packages_col.find({}).sort("credits", 1):
            pkgs.append(p)
        if not user:
            user = await get_or_create_user(user_id)
        current_balance = float(user.get("balance", 0))
        withdrawable_balance = float(user.get("withdrawable", 0))
        rows = []
        # Two packages per row; row 1 green, row 2 blue, alternating after that.
        for row_i, i in enumerate(range(0, len(pkgs), 2)):
            row = []
            style = "success" if row_i % 2 == 0 else "primary"
            for p in pkgs[i:i+2]:
                row.append(color_btn(f"₹{p['credits']} Credits", f"pkg:{p['credits']}", style))
            rows.append(row)
        rows.append([color_btn("✏️ ᴄᴜsᴛᴏᴍ ᴀᴍᴏᴜɴᴛ", "custom_deposit", "default")])
        rows.append([color_btn("🎫 sᴜᴘᴘᴏʀᴛ", "help", "default"), color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")])
        await event.edit(
            fancy(
                "💳 **Add Credits**\n\n"
                f"• Balance: `{current_balance:.2f} Cr`\n"
                f"• Withdrawable: `{withdrawable_balance:.2f} Cr`\n\n"
            ) + fancy_line(
                "⚡ instant & automated\n"
                "💡 rate: "
            ) + f"`1 Cr = ₹1`\n\n" + fancy_line("👇 select a credit package below:")
            ,
            buttons=rows
        )

    elif data.startswith("pkg:"):
        credits = int(data.split(":")[1])
        pkg = await packages_col.find_one({"credits": credits})
        if not pkg:
            await event.answer("❌ Package not found.", alert=True)
            return
        user_states[user_id] = {"deposit_pkg": credits, "pkg_data": pkg}
        methods = [
            [color_btn(f"⚡ {fancy('upi / qr')} – {fancy('auto')} – ₹{pkg['price']}", f"pay_method:auto:{credits}", "success")],
            [color_btn(f"📸 {fancy('upi / qr')} – {fancy('manual')} – ₹{pkg['price']}", f"pay_method:manual:{credits}", "primary")],
            [color_btn("◀️ ʙᴀᴄᴋ ᴛᴏ ᴘᴀᴄᴋᴀɢᴇs", "deposit", "default")]
        ]
        await event.edit(
            fancy(
                "💳 **Select Payment Method**\n\n"
                f"📦 Package: `{credits} Credits`\n"
                f"💰 Value: ₹{pkg['price']}\n\n"
                "⚡ **UPI / QR Auto** — Pay Via Live QR, Credited Automatically.\n"
                "📸 **UPI / QR Manual** — Pay, Send Screenshot, Admin Approves.\n"
                "💡 Rate: `1 Cr = ₹1`"
            ),
            buttons=methods
        )

    elif data.startswith("pay_method:"):
        _, method, credits_str = data.split(":")
        credits = int(credits_str)
        pkg = await packages_col.find_one({"credits": credits})
        if not pkg:
            await event.answer("❌ Package error.", alert=True)
            return
        amount = pkg["price"]

        if method == "auto":
            await _start_razorpay_auto_deposit(event, user_id, amount, credits)

        elif method == "manual":
            await _start_manual_deposit(event, user_id, amount, credits)

    elif data == "upi_app":
        st = user_states.get(user_id, {})
        upi_id = await get_setting("upi_id")
        amount = st.get("amount")
        if st.get("state") == "await_deposit_screenshot" and upi_id and amount:
            note = (f"📲 **Pay In Your UPI App**\n\nOpen Any UPI App → Send Money → UPI ID:\n`{upi_id}`\n"
                    f"Amount: `₹{float(amount):.2f}`\n\nThen Send The Screenshot Here.")
        else:
            note = ("📲 **Pay In Your UPI App**\n\nSave The QR Image Sent Above, Open Any UPI App → "
                    "Scan & Pay → Choose The QR From Gallery.")
        _track_temp(user_id, await event.respond(fancy(note)))
        await event.answer()

    elif data == "cancel_payment":
        user_states.pop(user_id, None)
        dep = await deposits_col.find_one({"user_id": user_id, "status": "pending"}, sort=[("_id", -1)])
        if dep:
            await deposits_col.update_one({"_id": dep["_id"]}, {"$set": {"status": "cancelled"}})
            if dep.get("qr_code_id"):
                await razorpay_close_qr(dep["qr_code_id"])
        await event.edit(
            fancy("✅ **Deposit Cancelled.** You Can Now Submit A New One."),
            buttons=[
                [color_btn("💳 ɴᴇᴡ ᴅᴇᴘᴏsɪᴛ", "deposit", "success")],
                [color_btn("◀️ ᴍᴀɪɴ ᴍᴇɴᴜ", "main_menu", "primary")],
            ]
        )

    elif data.startswith("pay_method_custom:"):
        method = data.split(":", 1)[1]
        state = user_states.get(user_id, {})
        amount = state.get("custom_amount")
        credits = state.get("custom_credits")
        if amount is None or credits is None:
            await event.answer("❌ No active custom deposit. Start again.", alert=True)
            return
        if method == "auto":
            await _start_razorpay_auto_deposit(event, user_id, amount, credits)
        else:
            await _start_manual_deposit(event, user_id, amount, credits)

    elif data in ("check_payment", "deposit_paid"):
        await _verify_deposit_ui(event, user_id)


    elif data == "custom_deposit":
        user_states[user_id] = {"state": "custom_deposit", "prompt_id": event.message_id}
        min_dep = await get_setting("min_deposit", 10.0)
        await event.edit(
            fancy(f"💡 **ᴄᴜsᴛᴏᴍ ᴀᴍᴏᴜɴᴛ**\n\nᴇɴᴛᴇʀ ᴛʜᴇ ᴀᴍᴏᴜɴᴛ ɪɴ ₹ (ᴍɪɴɪᴍᴜᴍ {min_dep}):"),
            buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")]]
        )

    # ── PROFILE ──────────────────────────────────────────────────
    elif data == "profile":
        if not user:
            user = await get_or_create_user(user_id)
        bal = float(user.get("balance", 0))
        wd = float(user.get("withdrawable", 0))
        orders_count = await orders_col.count_documents({"user_id": user_id})
        deps_count = await deposits_col.count_documents({"user_id": user_id})
        tier = user.get("tier", "⭐")
        lang = user.get("language", "en")
        await event.edit(
            fancy(
                f"👤 **ᴘʀᴏꜰɪʟᴇ**\n\n"
                f"🆔 ɪᴅ: `{user_id}`\n"
                f"🏅 ᴛɪᴇʀ: {tier}\n"
                f"💰 ᴀᴠᴀɪʟᴀʙʟᴇ ᴄʀᴇᴅɪᴛs: `{bal:.2f}`\n"
                f"💳 ᴡɪᴛʜᴅʀᴀᴡᴀʙʟᴇ: `{wd:.2f}`\n"
                f"📦 ᴏʀᴅᴇʀs: `{orders_count}`\n"
                f"💳 ᴅᴇᴘᴏsɪᴛs: `{deps_count}`\n"
                f"🌐 ʟᴀɴɢᴜᴀɢᴇ: `{lang}`"
            ),
            buttons=[
                [color_btn("📋 ᴏʀᴅᴇʀs", "orders", "primary"),
                 color_btn("📜 ʜɪsᴛᴏʀʏ", "history", "primary")],
                [color_btn("🎁 ʀᴇꜰᴇʀʀᴀʟ", "referral", "primary"),
                 color_btn("🌐 ʟᴀɴɢᴜᴀɢᴇ", "language", "primary")],
                [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]
            ]
        )

    # ── LANGUAGE ──────────────────────────────────────────────────
    elif data == "language":
        langs = [
            ("🇬🇧 English", "en"), ("🇮🇳 हिन्दी", "hi"), ("🇷🇺 Русский", "ru"),
            ("🇹🇷 Türkçe", "tr"), ("🇮🇳 தமிழ்", "ta"), ("🇮🇳 മലയാളം", "ml"),
        ]
        rows = []
        for label, code in langs:
            rows.append([color_btn(label, f"set_lang:{code}", "default")])
        rows.append([color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")])
        await event.edit(
            fancy("🌐 **ʟᴀɴɢᴜᴀɢᴇ sᴇᴛᴛɪɴɢs**\n\nᴄʜᴏᴏsᴇ ʏᴏᴜʀ ᴘʀᴇғᴇʀʀᴇᴅ ʟᴀɴɢᴜᴀɢᴇ:"),
            buttons=rows
        )

    elif data.startswith("set_lang:"):
        lang_code = data.split(":")[1]
        await users_col.update_one({"user_id": user_id}, {"$set": {"language": lang_code}})
        await event.answer(f"✅ Language set to {lang_code}", alert=True)
        await callback_router(event, "profile")

    # ── REFERRAL ──────────────────────────────────────────────────
    elif data == "referral":
        if not user:
            user = await get_or_create_user(user_id)
        pct      = float(await get_setting("referral_percent", 0))
        bonus    = float(await get_setting("referral_bonus", 0))
        ref_code = user.get("referral_code", "")
        earnings = float(user.get("referral_earnings", 0))
        count    = await users_col.count_documents({"referred_by": user_id})
        uname    = await get_bot_username()
        ref_link = f"https://t.me/{uname}?start=ref_{ref_code}"
        await event.edit(
            fancy(
                f"🎁 **ʀᴇꜰᴇʀ & ᴇᴀʀɴ**\n\n"
                f"ᴇᴀʀɴ **{pct:.1f}%** ᴏɴ ᴇᴠᴇʀʏ ᴅᴇᴘᴏsɪᴛ!\n"
                f"ᴘʟᴜs **₹{bonus:.0f}** ɪɴsᴛᴀɴᴛ ᴊᴏɪɴ ʙᴏɴᴜs!\n\n"
                f"🔗 **ʏᴏᴜʀ ʟɪɴᴋ:**\n`{ref_link}`\n\n"
                f"👥 ᴛᴏᴛᴀʟ ʀᴇꜰᴇʀʀᴇᴅ: **{count}**\n"
                f"💰 ᴛᴏᴛᴀʟ ᴇᴀʀɴᴇᴅ: `₹{earnings:.2f}`"
            ),
            buttons=[
                [Button.url("📤 sʜᴀʀᴇ ʟɪɴᴋ", f"https://t.me/share/url?url={ref_link}&text=Buy+Telegram+accounts+instantly!")],
                [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")],
            ],
        )

    # ── HELP ─────────────────────────────────────────────────────
    elif data == "help":
        await event.edit(
            fancy("❓ **ʜᴇʟᴘ ᴄᴇɴᴛᴇʀ**\n\nᴄʜᴏᴏsᴇ ᴀ ᴛᴏᴘɪᴄ ʙᴇʟᴏᴡ:"),
            buttons=[
                [color_btn("📖 ʜᴏᴡ ᴛᴏ ᴜsᴇ", "help_howto", "default")],
                [color_btn("🔑 ᴏᴛᴘ ʜᴇʟᴘ", "help_otp", "default")],
                [color_btn("💳 ᴘᴀʏᴍᴇɴᴛ ʜᴇʟᴘ", "help_payment", "default")],
                [color_btn("🛡️ ᴡʜʏ ᴛʀᴜsᴛ ᴜs?", "help_trust", "default")],
                [color_btn("📞 ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ", "support", "default")],
                [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]
            ]
        )

    elif data == "help_howto":
        await event.edit(
            fancy("📖 **ʜᴏᴡ ᴛᴏ ᴜsᴇ**\n\n"
                  "1️⃣ Add Credits Via UPI / QR.\n"
                  "2️⃣ ɢᴏ ᴛᴏ sᴛᴏʀᴇ → sᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ.\n"
                  "3️⃣ ᴄʜᴏᴏsᴇ ᴀ ᴄᴏᴜɴᴛʀʏ → ᴛᴀᴘ ʙᴜʏ.\n"
                  "4️⃣ ʀᴇᴄᴇɪᴠᴇ ᴏᴛᴘ & 2ꜰᴀ ɪɴsᴛᴀɴᴛʟʏ.\n"
                  "5️⃣ ʀᴇ-ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ ᴡɪᴛʜɪɴ 24ʜ."),
            buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "help", "default")]]
        )

    elif data == "help_otp":
        await event.edit(
            fancy("🔑 **ᴏᴛᴘ ᴛʀᴏᴜʙʟᴇsʜᴏᴏᴛɪɴɢ**\n\n"
                  "⚠️ ᴄᴏᴅᴇ ɴᴏᴛ ᴀʀʀɪᴠᴇᴅ?\n→ ʀᴇǫᴜᴇsᴛ ᴀɢᴀɪɴ.\n\n"
                  "❓ ᴡʜᴇʀᴇ ɪs 2ꜰᴀ?\n→ ᴄʜᴇᴄᴋ ᴏʀᴅᴇʀ ᴅᴇᴛᴀɪʟs."),
            buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "help", "default")]]
        )

    elif data == "help_payment":
        await event.edit(
            fancy("💳 **ᴘᴀʏᴍᴇɴᴛ ʜᴇʟᴘ**\n\n"
                  "| ᴍᴇᴛʜᴏᴅ | ᴛɪᴍᴇ |\n"
                  "⚡ UPI / QR Auto-Verify — Credited In 1–2 Min.\n"
                  "📸 UPI / QR Manual — Credited After Admin Approval.\n\n"
                  "✔ Paid But Credits Not Added?\n→ Tap 'Check Payment' Or Contact Support."),
            buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "help", "default")]]
        )

    elif data == "help_trust":
        await event.edit(
            fancy("🛡️ **ᴡʜʏ ᴛʀᴜsᴛ ᴏᴛᴘ ʙᴏᴛ?**\n\n🔹 100% ᴀᴜᴛᴏᴍᴀᴛᴇᴅ\n🔹 ɪɴsᴛᴀɴᴛ ᴏᴛᴘ ғᴏʀᴡᴀʀᴅɪɴɢ\n🔹 ɴᴏ ʜᴜᴍᴀɴ sᴇᴇs sᴇssɪᴏɴ ᴅᴀᴛᴀ"),
            buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "help", "default")]]
        )

    elif data == "support":
        support_link = await get_setting("support_link")
        if support_link:
            await event.edit(fancy("📞 **ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ**\n\nᴛᴀᴘ ʙᴇʟᴏᴡ ᴛᴏ ᴄʜᴀᴛ."),
                buttons=[[Button.url("📞 sᴜᴘᴘᴏʀᴛ", support_link)], [color_btn("◀️ ʙᴀᴄᴋ", "help", "default")]])
        else:
            await event.edit(fancy("📞 **sᴜᴘᴘᴏʀᴛ**\n\nɴᴏ sᴜᴘᴘᴏʀᴛ ʟɪɴᴋ ᴄᴏɴғɪɢᴜʀᴇᴅ."),
                buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "help", "default")]])

    # ── WHATSAPP ──────────────────────────────────────────────────
    elif data == "whatsapp":
        await event.edit(
            fancy("💬 **ᴡʜᴀᴛsᴀᴘᴘ**\n\nᴄᴏɴᴛᴀᴄᴛ ᴏᴜʀ ᴡʜᴀᴛsᴀᴘᴘ sᴜᴘᴘᴏʀᴛ ғᴏʀ ᴀssɪsᴛᴀɴᴄᴇ."),
            buttons=[[color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]]
        )

    # ════════════════════════════════════════════
    #  ADMIN CALLBACKS
    # ════════════════════════════════════════════
    elif data == "admin":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        owner_flag = (user_id == OWNER_ID)
        await event.edit(
            fancy("⚙️ **ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ**\n\nsᴇʟᴇᴄᴛ ᴀᴄᴛɪᴏɴ:"),
            buttons=_admin_menu_buttons(owner_flag),
        )

    elif data == "astats":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        total_users  = await users_col.count_documents({})
        total_acc    = await accounts_col.count_documents({})
        avail_acc    = await accounts_col.count_documents({"status": "available"})
        total_orders = await orders_col.count_documents({})
        pending_deps = await deposits_col.count_documents({"status": "pending"})
        banned       = await users_col.count_documents({"is_banned": True})
        rev_pipe     = await deposits_col.aggregate([
            {"$match": {"status": "approved"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]).to_list(1)
        revenue  = rev_pipe[0]["total"] if rev_pipe else 0
        today    = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        new_today = await users_col.count_documents({"joined_at": {"$gte": today}})
        db_admins = await bot_admins_col.count_documents({"is_active": True})
        await event.edit(
            fancy(
                f"📊 **ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs**\n\n"
                f"👥 ᴛᴏᴛᴀʟ ᴜsᴇʀs: `{total_users}` (+{new_today} ᴛᴏᴅᴀʏ)\n"
                f"🚫 ʙᴀɴɴᴇᴅ: `{banned}`\n"
                f"🔑 ᴇxᴛʀᴀ ᴀᴅᴍɪɴs: `{db_admins}`\n\n"
                f"📦 ᴛᴏᴛᴀʟ sᴇssɪᴏɴs: `{total_acc}`\n"
                f"✅ ᴀᴠᴀɪʟᴀʙʟᴇ: `{avail_acc}`\n"
                f"🛒 ᴛᴏᴛᴀʟ ᴏʀᴅᴇʀs: `{total_orders}`\n\n"
                f"💳 ᴘᴇɴᴅɪɴɢ ᴅᴇᴘᴏsɪᴛs: `{pending_deps}`\n"
                f"💰 ᴛᴏᴛᴀʟ ʀᴇᴠᴇɴᴜᴇ: `₹{revenue:.2f}`"
            ),
            buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")]],
        )

    elif data == "upload_sessions":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        server_count = int(await get_setting("server_count", 2))
        rows = []
        row = []
        for i in range(1, server_count + 1):
            row.append(color_btn(f"Server {i}", f"upload_srv:{i}", "primary"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")])
        await event.edit(fancy("📦 **ᴜᴘʟᴏᴀᴅ sᴇssɪᴏɴs**\n\nsᴇʟᴇᴄᴛ ᴡʜɪᴄʜ sᴇʀᴠᴇʀ ᴛʜɪs sᴛᴏᴄᴋ ʙᴇʟᴏɴɢs ᴛᴏ:"), buttons=rows)

    elif data.startswith("upload_srv:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        srv_n = data.split(":", 1)[1]
        cats = await get_categories()
        rows = []
        for cat in cats:
            rows.append([color_btn(f"{cat['icon']} {cat['name']}", f"upload_cat:{srv_n}:{cat['name']}", "default")])
        rows.append([color_btn("◀️ ʙᴀᴄᴋ", "upload_sessions", "default")])
        note = "" if srv_n == "1" else "\n\n⚠️ ɴᴏᴛᴇ: sᴇʀᴠᴇʀs ᴏᴛʜᴇʀ ᴛʜᴀɴ 1 ᴀʀᴇɴ'ᴛ ᴄᴏɴɴᴇᴄᴛᴇᴅ ʏᴇᴛ — sᴛᴏᴄᴋ ᴡᴏɴ'ᴛ ʙᴇ sᴏʟᴅ ᴜɴᴛɪʟ ᴛʜᴀᴛ sᴇʀᴠᴇʀ ɪs ᴡɪʀᴇᴅ ᴜᴘ."
        await event.edit(fancy(f"📦 **ᴜᴘʟᴏᴀᴅ ᴛᴏ sᴇʀᴠᴇʀ {srv_n}**\n\nsᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ:" + note), buttons=rows)

    elif data.startswith("upload_cat:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        _, srv_n, cat_name = data.split(":", 2)
        c_list = await countries_col.find({"is_active": True}).to_list(50)
        rows = []
        for c in c_list:
            rows.append([color_btn(f"{c['flag']} {c['name']}", f"upload_country:{srv_n}:{c['code']}:{cat_name}", "default")])
        rows.append([color_btn("◀️ ʙᴀᴄᴋ", f"upload_srv:{srv_n}", "default")])
        await event.edit(fancy(f"📦 **ᴜᴘʟᴏᴀᴅ ᴛᴏ {cat_name} (sᴇʀᴠᴇʀ {srv_n})**\n\nsᴇʟᴇᴄᴛ ᴄᴏᴜɴᴛʀʏ:"), buttons=rows)

    elif data.startswith("upload_country:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        parts = data.split(":", 3)
        if len(parts) < 4:
            await event.answer("❌ Error.", alert=True)
            return
        _, srv_n, cc, cat_name = parts
        year_cfg = await _server_year_cfg(int(srv_n))
        if year_cfg["enabled"]:
            per_row = await _per_row()
            rows, row = [], []
            for y in year_cfg["years"]:
                row.append(color_btn(y, f"upload_year:{srv_n}:{cc}:{cat_name}:{y}", "default"))
                if len(row) == per_row:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)
            rows.append([color_btn("◀️ ʙᴀᴄᴋ", f"upload_cat:{srv_n}:{cat_name}", "default")])
            await event.edit(fancy("📅 **Upload — Select Year**\n\nWhich Year Are These Accounts From?"), buttons=rows)
            return
        await _begin_upload(event, user_id, srv_n, cc, cat_name)

    elif data.startswith("upload_year:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        _, srv_n, cc, cat_name, yr = data.split(":", 4)
        await _begin_upload(event, user_id, srv_n, cc, cat_name, yr)

    elif data == "manage_sessions":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        total   = await accounts_col.count_documents({})
        avail   = await accounts_col.count_documents({"status": "available"})
        sold    = await accounts_col.count_documents({"status": "sold"})
        logout_ = await accounts_col.count_documents({"status": "logged_out"})
        pipe = await accounts_col.aggregate([
            {"$match": {"status": "available"}},
            {"$group": {"_id": "$country", "count": {"$sum": 1}, "flag": {"$first": "$country_flag"}}},
            {"$sort": {"count": -1}},
        ]).to_list(20)
        by_country = "\n".join(
            f"  {r.get('flag','🌍')} {r['_id']}: `{r['count']}`"
            for r in pipe
        ) or "  (ᴇᴍᴘᴛʏ)"
        await event.edit(
            fancy(
                f"📋 **sᴇssɪᴏɴ ᴏᴠᴇʀᴠɪᴇᴡ**\n\n"
                f"ᴛᴏᴛᴀʟ: `{total}`\n"
                f"✅ ᴀᴠᴀɪʟᴀʙʟᴇ: `{avail}`\n"
                f"🔑 sᴏʟᴅ: `{sold}`\n"
                f"🔓 ʟᴏɢɢᴇᴅ ᴏᴜᴛ: `{logout_}`\n\n"
                f"**ᴀᴠᴀɪʟᴀʙʟᴇ ʙʏ ᴄᴏᴜɴᴛʀʏ:**\n{by_country}"
            ),
            buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")]],
        )

    elif data == "pending_deposits":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        deps = await deposits_col.find({"status": "pending"}).sort("created_at", 1).limit(10).to_list(10)
        if not deps:
            await event.edit(fancy("✅ ɴᴏ ᴘᴇɴᴅɪɴɢ ᴅᴇᴘᴏsɪᴛs."),
                buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")]])
            return
        await event.answer()
        for dep in deps:
            from bson import ObjectId
            dep_id  = str(dep["_id"])
            uid     = dep["user_id"]
            amount  = dep["amount"]
            created = dep["created_at"].strftime("%d %b %H:%M")
            await bot.send_message(
                user_id,
                fancy(f"💳 **ᴅᴇᴘᴏsɪᴛ ʀᴇǫᴜᴇsᴛ**\n• ᴜsᴇʀ ɪᴅ: `{uid}`\n• ᴀᴍᴏᴜɴᴛ: `₹{amount:.2f}`\n• ᴛɪᴍᴇ: {created}"),
                buttons=[
                    [color_btn("✅ ᴀᴘᴘʀᴏᴠᴇ", f"dep_approve:{dep_id}:{uid}:{amount}", "success"),
                     color_btn("❌ ʀᴇᴊᴇᴄᴛ", f"dep_reject:{dep_id}:{uid}", "danger")],
                ],
            )

    elif data.startswith("dep_approve:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        from bson import ObjectId
        _, dep_id, uid_str, amount_str = data.split(":", 3)
        uid    = int(uid_str)
        amount = float(amount_str)

        claim = await deposits_col.update_one(
            {"_id": ObjectId(dep_id), "status": "pending"},
            {"$set": {"status": "approved", "approved_at": datetime.utcnow(), "approved_by": user_id}},
        )
        if claim.matched_count == 0:
            await event.answer("⚠️ Already processed.", alert=True)
            return

        dep_doc = await deposits_col.find_one({"_id": ObjectId(dep_id)})
        if dep_doc:
            uid    = int(dep_doc.get("user_id", uid))
            amount = float(dep_doc.get("amount", amount))
            credits = dep_doc.get("credits", 0)

        buyer = await users_col.find_one({"user_id": uid})
        if buyer and buyer.get("referred_by"):
            pct   = float(await get_setting("referral_percent", 0))
            bonus = round(amount * pct / 100, 2)
            if bonus > 0:
                await users_col.update_one(
                    {"user_id": buyer["referred_by"]},
                    {"$inc": {"balance": bonus, "referral_earnings": bonus, "withdrawable": bonus}},
                )
                try:
                    await bot.send_message(
                        buyer["referred_by"],
                        fancy(f"💸 **ʀᴇꜰᴇʀʀᴀʟ ᴇᴀʀɴɪɴɢ!**\n+₹{bonus:.2f} ғʀᴏᴍ ᴅᴇᴘᴏsɪᴛ."),
                    )
                except Exception:
                    pass

        await users_col.update_one({"user_id": uid}, {"$inc": {"balance": amount, "withdrawable": amount}})
        try:
            await bot.send_message(uid, fancy(f"✅ **ᴅᴇᴘᴏsɪᴛ ᴀᴘᴘʀᴏᴠᴇᴅ!**\n`₹{amount:.2f}` ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ."))
        except Exception:
            pass
        await log_event(f"✅ **ᴍᴀɴᴜᴀʟ ᴅᴇᴘᴏsɪᴛ ᴀᴘᴘʀᴏᴠᴇᴅ**\n• ᴜsᴇʀ: `{uid}`\n• ₹{amount:.2f} → +{dep_doc.get('credits', 0) if dep_doc else 0} ᴄʀ\n• ʙʏ ᴀᴅᴍɪɴ: `{user_id}`")
        await event.edit(f"✅ ᴀᴘᴘʀᴏᴠᴇᴅ ₹{amount:.2f} ғᴏʀ ᴜsᴇʀ `{uid}`.")

    elif data.startswith("dep_reject:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        from bson import ObjectId
        _, dep_id, uid_str = data.split(":", 2)
        uid = int(uid_str)
        dep_doc = await deposits_col.find_one_and_update(
            {"_id": ObjectId(dep_id), "status": "pending"},
            {"$set": {"status": "rejected", "rejected_at": datetime.utcnow(), "rejected_by": user_id}},
            return_document=True,
        )
        if dep_doc is None:
            await event.answer("⚠️ Already processed.", alert=True)
            return
        uid = int(dep_doc.get("user_id", uid))
        try:
            await bot.send_message(uid, fancy("❌ **ᴅᴇᴘᴏsɪᴛ ʀᴇᴊᴇᴄᴛᴇᴅ.**\nᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ."))
        except Exception:
            pass
        await event.edit(f"❌ ʀᴇᴊᴇᴄᴛᴇᴅ ᴅᴇᴘᴏsɪᴛ ғᴏʀ ᴜsᴇʀ `{uid}`.")

    elif data == "broadcast":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "broadcast"}
        await event.edit(fancy("📢 **ʙʀᴏᴀᴅᴄᴀsᴛ**\n\nsᴇɴᴅ ᴛʜᴇ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ᴛᴏ ᴀʟʟ ᴜsᴇʀs."),
            buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "admin", "danger")]])

    elif data == "asettings":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        upi    = await get_setting("upi_id",         "Not set")
        uname_ = await get_setting("upi_name",        "Prime Vault")
        sup    = await get_setting("support_link",    "Not set")
        bn     = await get_setting("bot_name",        "Prime Vault")
        rb     = await get_setting("referral_bonus",  0)
        rp     = await get_setting("referral_percent", 0)
        md     = await get_setting("min_deposit",     10)
        wa     = "✅" if await get_setting("whatsapp_enabled") else "❌"
        photo  = "✅" if await get_setting("welcome_photo") else "❌"
        rzp    = "✅ ᴄᴏɴғɪɢᴜʀᴇᴅ" if (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET) else "❌ ɴᴏᴛ sᴇᴛ (.ᴇɴᴠ)"
        vnh_on = "✅ ᴄᴏɴɴᴇᴄᴛᴇᴅ" if vnh_server.configured else "❌ ɴᴏᴛ sᴇᴛ (.ᴇɴᴠ)"
        vnh_mk = await vnh_server.get_markup_percent()
        skip1  = "✅" if await get_setting("skip_server_step_if_one", True) else "❌"
        logs_ch = LOGS_CHANNEL_ID or await get_setting("logs_channel_id", 0)
        default2fa = await get_setting("default_2fa", "")
        srv_count = await get_setting("server_count", 2)
        server_count_ = int(await get_setting("server_count", 2))
        year_lines = []
        for i in range(1, server_count_ + 1):
            c_ = await _server_year_cfg(i)
            active_ = await _server_active(i)
            year_lines.append(f"Server {i}: {'🟢 On' if active_ else '🔴 Off'} • Year {'✅' if c_['enabled'] else '❌'} (`{', '.join(c_['years'])}`)")
        per_row_v = await _per_row()
        await event.edit(
            fancy(
                f"⚙️ **sᴇᴛᴛɪɴɢs**\n\n"
                f"• ʙᴏᴛ ɴᴀᴍᴇ: `{bn}`\n"
                f"• ᴜᴘɪ ɪᴅ: `{upi}`\n"
                f"• ᴜᴘɪ ɴᴀᴍᴇ: `{uname_}`\n"
                f"• sᴜᴘᴘᴏʀᴛ: `{sup}`\n"
                f"• ʀᴇꜰ ʙᴏɴᴜs: `₹{rb}`\n"
                f"• ʀᴇꜰ %: `{rp}%`\n"
                f"• ᴍɪɴ ᴅᴇᴘᴏsɪᴛ: `₹{md}`\n"
                f"• ᴡʜᴀᴛsᴀᴘᴘ: {wa}\n"
                f"• ᴡᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ: {photo}\n"
                f"• ʀᴀᴢᴏʀᴘᴀʏ ᴀᴜᴛᴏ-ᴠᴇʀɪғʏ: {rzp}\n"
                f"• ᴠɴʜ sᴜᴘᴘʟɪᴇʀ (sᴇʀᴠᴇʀ {await get_setting('vnh_server_number', 2)}): {vnh_on} • ᴍᴀʀᴋᴜᴘ `{vnh_mk}%`\n"
                f"• sᴋɪᴘ 1-sᴇʀᴠᴇʀ sᴛᴇᴘ: {skip1}\n"
                f"• ʟᴏɢs ᴄʜᴀɴɴᴇʟ: `{logs_ch or 'ɴᴏᴛ sᴇᴛ'}`\n"
                f"• ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ: `{default2fa or 'ɴᴏɴᴇ'}`\n"
                f"• sᴇʀᴠᴇʀs: `{srv_count}`\n"
                + "\n".join(f"• {l}" for l in year_lines) + "\n"
                f"• Buttons Per Row: `{per_row_v}`"
            ),
            buttons=[
                [color_btn("🤖 ʙᴏᴛ ɴᴀᴍᴇ", "set_botname", "primary"), color_btn("💳 ᴜᴘɪ ɪᴅ", "set_upi", "primary")],
                [color_btn("👤 ᴜᴘɪ ɴᴀᴍᴇ", "set_upiname", "primary"), color_btn("📞 sᴜᴘᴘᴏʀᴛ", "set_support", "primary")],
                [color_btn("🎁 ʀᴇꜰ ʙᴏɴᴜs", "set_ref_bonus", "primary"), color_btn("📈 ʀᴇꜰ %", "set_ref_pct", "primary")],
                [color_btn("🔢 ᴍɪɴ ᴅᴇᴘᴏsɪᴛ", "set_min_dep", "primary"), color_btn("🖥️ sᴇʀᴠᴇʀs", "set_server_count", "primary")],
                [color_btn("💬 ᴛᴏɢɢʟᴇ ᴡʜᴀᴛsᴀᴘᴘ", "toggle_whatsapp", "primary"), color_btn("🖼️ sᴇᴛ ᴡᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ", "set_welcome_photo", "primary")],
                [color_btn("📋 ʟᴏɢs ᴄʜᴀɴɴᴇʟ", "set_logs_channel", "primary"), color_btn("🔐 ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ", "set_default_2fa", "primary")],
                [color_btn("🏷️ ꜱᴇʀᴠᴇʀ ʟᴀʙᴇʟs", "set_server_labels", "primary"), color_btn("🔌 ᴛᴏɢɢʟᴇ ꜱᴇʀᴠᴇʀ", "set_toggle_server", "primary")],
                [color_btn("📅 ʏᴇᴀʀ sᴇᴛᴛɪɴɢs", "set_year_cfg", "primary")],
                [color_btn("🔲 ʙᴜᴛᴛᴏɴs/ʀᴏᴡ", "set_per_row", "primary"), color_btn("📈 ᴠɴʜ ᴍᴀʀᴋᴜᴘ %", "set_vnh_markup", "primary")],
                [color_btn("⏭️ sᴋɪᴘ 1-sᴇʀᴠᴇʀ sᴛᴇᴘ", "toggle_skip_server", "primary"), color_btn("🗑️ ʀᴇᴍᴏᴠᴇ ᴜᴘɪ ɪᴅ", "remove_upi", "danger")],
                [color_btn("⭐ ᴠɪᴘ ᴅɪsᴄᴏᴜɴᴛ %", "set_vip_discount", "primary")],
                [color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")],
            ],
        )

    elif data in (
        "set_botname", "set_upi", "set_upiname", "set_support",
        "set_ref_bonus", "set_ref_pct", "set_min_dep",
        "toggle_whatsapp", "set_welcome_photo",
        "set_default_2fa", "set_server_count", "set_logs_channel",
        "set_server_labels", "set_toggle_server", "set_year_cfg", "set_per_row", "set_vnh_markup",
        "toggle_skip_server", "remove_upi"
    ):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        _prompts = {
            "set_botname":      ("setting_botname",  "🤖 sᴇɴᴅ ɴᴇᴡ ʙᴏᴛ ɴᴀᴍᴇ:"),
            "set_upi":          ("setting_upi",       "💳 sᴇɴᴅ ɴᴇᴡ ᴜᴘɪ ɪᴅ:"),
            "set_upiname":      ("setting_upiname",   "👤 sᴇɴᴅ ᴜᴘɪ ᴅɪsᴘʟᴀʏ ɴᴀᴍᴇ:"),
            "set_support":      ("setting_support",   "📞 sᴇɴᴅ sᴜᴘᴘᴏʀᴛ ʟɪɴᴋ:"),
            "set_ref_bonus":    ("setting_ref_bonus", "🎁 sᴇɴᴅ ʀᴇꜰᴇʀʀᴀʟ ᴊᴏɪɴ ʙᴏɴᴜs (₹):"),
            "set_ref_pct":      ("setting_ref_pct",   "📈 sᴇɴᴅ ʀᴇꜰᴇʀʀᴀʟ ᴅᴇᴘᴏsɪᴛ %:"),
            "set_min_dep":      ("setting_min_dep",   "🔢 sᴇɴᴅ ᴍɪɴɪᴍᴜᴍ ᴅᴇᴘᴏsɪᴛ (₹):"),
            "toggle_whatsapp":  ("whatsapp_toggle",   None),
            "set_welcome_photo":("welcome_photo_set", "🖼️ sᴇɴᴅ ᴛʜᴇ ᴘʜᴏᴛᴏ ᴛᴏ ᴜsᴇ ᴀs ᴡᴇʟᴄᴏᴍᴇ ɪᴍᴀɢᴇ."),
            "set_default_2fa":  ("setting_default_2fa", "🔐 sᴇɴᴅ ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ:"),
            "set_server_count": ("setting_server_count", "🖥️ sᴇɴᴅ ᴛᴏᴛᴀʟ sᴇʀᴠᴇʀ ᴄᴏᴜɴᴛ (ᴇ.ɢ. 2):"),
            "set_server_labels": ("setting_server_labels", "🏷️ Send Server Labels, One Per Line:\n`Server | Tag | Bullet1; Bullet2; Bullet3`\nExample:\n`1 | Private Stock | Cheapest option; Directly managed`"),
            "set_toggle_server": ("setting_toggle_server", "🔌 Send: `Server | on` Or `Server | off`\nExample: `2 | on`"),
            "set_vnh_markup":   ("setting_vnh_markup", "📈 Send The VNH Markup Percent (e.g. `20` For 20%):"),
            "set_vip_discount": ("setting_vip_discount", "⭐ Send The VIP Discount Percent (e.g. `10` For 10% Off):"),
            "set_year_cfg":     ("setting_year_cfg", "📅 Send: `Server | on/off | Year1,Year2,...`\nExample: `1 | on | 2021,2022,2023`\nExample: `2 | off`"),
            "set_per_row":      ("setting_per_row", "🔲 Send Buttons Per Row (1 To 4):"),
            "set_logs_channel": ("setting_logs_channel", "📋 ꜰᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ꜰʀᴏᴍ ᴛʜᴇ ʟᴏɢs ᴄʜᴀɴɴᴇʟ, ᴏʀ sᴇɴᴅ ɪᴛs ᴄʜᴀᴛ ɪᴅ (ᴇ.ɢ. -100123456789).\nᴍᴀᴋᴇ sᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪs ᴀᴅᴅᴇᴅ ᴛᴏ ᴛʜᴀᴛ ᴄʜᴀɴɴᴇʟ ᴀs ᴀᴅᴍɪɴ ꜰɪʀsᴛ:"),
        }
        sk, prompt = _prompts[data]
        if data == "toggle_whatsapp":
            current = await get_setting("whatsapp_enabled", False)
            await set_setting("whatsapp_enabled", not current)
            await event.answer(f"✅ WhatsApp {'enabled' if not current else 'disabled'}")
            await event.edit(fancy("💬 **ᴡʜᴀᴛsᴀᴘᴘ** ᴛᴏɢɢʟᴇᴅ."),
                             buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "asettings", "default")]])
            return
        elif data == "toggle_skip_server":
            cur = await get_setting("skip_server_step_if_one", True)
            await set_setting("skip_server_step_if_one", not cur)
            await event.answer(f"Skip-If-One-Server: {'ON' if not cur else 'OFF'}")
            await callback_router(event, "asettings")
            return
        elif data == "remove_upi":
            await set_setting("upi_id", "")
            await event.answer("✅ UPI ID removed.")
            await callback_router(event, "asettings")
            return
        elif data == "set_welcome_photo":
            user_states[user_id] = {"state": "welcome_photo_set"}
            await event.edit(prompt, buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "asettings", "danger")]])
            return
        user_states[user_id] = {"state": sk}
        await event.edit(fancy(prompt), buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "asettings", "danger")]])

    elif data == "acountries":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        c_list = await countries_col.find({}).to_list(50)
        rows   = []
        for c in c_list:
            em = "✅" if c.get("is_active") else "❌"
            rows.append([color_btn(f"{em} {c['flag']} {c['name']} — {c['price']} ᴄʀ", f"ctoggle:{c['code']}", "default")])
        rows.append([color_btn("➕ ᴀᴅᴅ ᴄᴏᴜɴᴛʀʏ", "add_country", "primary"),
                     color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")])
        await event.edit(fancy("🌍 **ᴄᴏᴜɴᴛʀɪᴇs** (ᴛᴀᴘ ᴛᴏ ᴛᴏɢɢʟᴇ):"), buttons=rows)

    elif data.startswith("ctoggle:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        code = data.split(":")[1]
        c    = await countries_col.find_one({"code": code})
        if not c:
            await event.answer("❌ Not found.", alert=True)
            return
        new  = not c.get("is_active", True)
        await countries_col.update_one({"code": code}, {"$set": {"is_active": new}})
        await event.answer(f"{'Enabled' if new else 'Disabled'} {code}")
        await callback_router(event, "acountries")

    elif data == "acategories":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        cat_list = await categories_col.find({}).sort("order", 1).to_list(50)
        rows = []
        for cat in cat_list:
            em = "✅" if cat.get("is_active") else "❌"
            rows.append([color_btn(f"{em} #{cat.get('order', 0)} {cat['icon']} {cat['name']}", f"cattoggle:{cat['name']}", "default"),
                         color_btn("🗑️", f"catdel:{cat['name']}", "danger")])
        rows.append([color_btn("➕ ᴀᴅᴅ ᴄᴀᴛᴇɢᴏʀʏ", "add_category", "primary"),
                     color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")])
        await event.edit(fancy("🗂️ **ᴄᴀᴛᴇɢᴏʀɪᴇs** (ᴛᴀᴘ ᴛᴏ ᴛᴏɢɢʟᴇ):"), buttons=rows)

    elif data.startswith("cattoggle:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        name = data.split(":", 1)[1]
        cat  = await categories_col.find_one({"name": name})
        if not cat:
            await event.answer("❌ Not found.", alert=True)
            return
        new = not cat.get("is_active", True)
        await categories_col.update_one({"name": name}, {"$set": {"is_active": new}})
        await event.answer(f"{'Enabled' if new else 'Disabled'} {name}")
        await callback_router(event, "acategories")

    elif data.startswith("catdel:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        name = data.split(":", 1)[1]
        stock_left = await accounts_col.count_documents({"category": name, "status": "available"})
        if stock_left > 0:
            await event.answer(f"❌ {stock_left} account(s) still use this category — disable it instead of deleting.", alert=True)
            return
        await categories_col.delete_one({"name": name})
        await event.answer(f"Deleted {name}")
        await callback_router(event, "acategories")

    elif data == "add_category":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "add_category"}
        await event.edit(
            fancy("🗂️ **ᴀᴅᴅ / ᴇᴅɪᴛ ᴄᴀᴛᴇɢᴏʀʏ**\n\nsᴇɴᴅ:\n`Name | Icon | Order (optional)`\nᴇxᴀᴍᴘʟᴇ: `Premium | 💎 | 1`\n\nᴜsɪɴɢ ᴀɴ ᴇxɪsᴛɪɴɢ ɴᴀᴍᴇ ᴜᴘᴅᴀᴛᴇs ɪᴛ (ᴀɴᴅ ʀᴇ-ᴇɴᴀʙʟᴇs ɪᴛ ɪꜰ ᴏꜰꜰ)."),
            buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "acategories", "danger")]],
        )

    elif data == "add_country":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "add_country"}
        await event.edit(
            fancy("🌍 **ᴀᴅᴅ ᴄᴏᴜɴᴛʀʏ**\n\nsᴇɴᴅ:\n`CODE | Name | Flag | Price`\nᴇxᴀᴍᴘʟᴇ: `TR | Turkey | 🇹🇷 | 28`"),
            buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "acountries", "danger")]],
        )

    elif data == "ausers":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "search_user"}
        await event.edit(
            fancy("👤 **ᴜsᴇʀ ʟᴏᴏᴋᴜᴘ**\n\nsᴇɴᴅ ᴛʜᴇ ᴛᴇʟᴇɢʀᴀᴍ ᴜsᴇʀ ɪᴅ:"),
            buttons=[
                [color_btn("➕ ᴀᴅᴅ ʙᴀʟᴀɴᴄᴇ", "admin_add_bal", "primary"),
                 color_btn("🚫 ʙᴀɴ/ᴜɴʙᴀɴ", "admin_ban", "primary")],
                [color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")],
            ],
        )

    elif data == "admin_add_bal":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "add_bal_uid"}
        await event.edit(fancy("💰 **ᴀᴅᴅ ʙᴀʟᴀɴᴄᴇ**\n\nsᴇɴᴅ ᴜsᴇʀ ɪᴅ:"),
            buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "admin", "danger")]])

    elif data == "admin_ban":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "ban_uid"}
        await event.edit(fancy("🚫 **ʙᴀɴ/ᴜɴʙᴀɴ**\n\nsᴇɴᴅ ᴜsᴇʀ ɪᴅ:"),
            buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "admin", "danger")]])

    elif data == "manage_admins":
        if user_id != OWNER_ID:
            await event.answer("❌ Owner only!", alert=True)
            return
        admins = await bot_admins_col.find({"is_active": True}).to_list(50)
        rows   = []
        for a in admins:
            rows.append([color_btn(f"🔴 ʀᴇᴍᴏᴠᴇ {a.get('name', a['telegram_id'])}", f"rm_admin:{a['telegram_id']}", "danger")])
        rows.append([color_btn("➕ ᴀᴅᴅ ᴀᴅᴍɪɴ", "add_admin", "primary"),
                     color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")])
        await event.edit(fancy(f"🔑 **ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs**\n\nᴏᴡɴᴇʀ: `{OWNER_ID}`\nᴇxᴛʀᴀ ᴀᴅᴍɪɴs: {len(admins)}"), buttons=rows)

    elif data == "add_admin":
        if user_id != OWNER_ID:
            await event.answer("❌ Owner only!", alert=True)
            return
        user_states[user_id] = {"state": "add_admin"}
        await event.edit(fancy("🔑 **ᴀᴅᴅ ᴀᴅᴍɪɴ**\n\nsᴇɴᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴜsᴇʀ ɪᴅ:"),
            buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "manage_admins", "danger")]])

    elif data.startswith("rm_admin:"):
        if user_id != OWNER_ID:
            await event.answer("❌ Owner only!", alert=True)
            return
        rm_id = int(data.split(":")[1])
        await bot_admins_col.update_one({"telegram_id": rm_id}, {"$set": {"is_active": False}})
        await event.answer(f"Removed admin {rm_id}")
        try:
            await bot.send_message(rm_id, "🔑 Your admin access has been removed.")
        except Exception:
            pass
        admins = await bot_admins_col.find({"is_active": True}).to_list(50)
        rows   = [[color_btn(f"🔴 ʀᴇᴍᴏᴠᴇ {a.get('name', a['telegram_id'])}", f"rm_admin:{a['telegram_id']}", "danger")] for a in admins]
        rows.append([color_btn("➕ ᴀᴅᴅ ᴀᴅᴍɪɴ", "add_admin", "primary"),
                     color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")])
        await event.edit(fancy("🔑 **ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs**:"), buttons=rows)

    elif data == "balance":
        if not user:
            user = await get_or_create_user(user_id)
        bal   = float(user.get("balance", 0))
        spent = 0.0
        async for o in orders_col.find({"user_id": user_id, "status": {"$nin": ["cancelled"]}}):
            spent += float(o.get("amount", 0))
        await event.edit(
            fancy(f"💰 **ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ**\n\nᴀᴠᴀɪʟᴀʙʟᴇ: `{bal:.2f} ᴄʀ`\nᴛᴏᴛᴀʟ sᴘᴇɴᴛ: `{spent:.2f} ᴄʀ`"),
            buttons=[
                [color_btn("💳 ᴀᴅᴅ ᴄʀᴇᴅɪᴛs", "deposit", "success")],
                [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")],
            ],
        )

    else:
        await event.answer()

# ─── 19. DEPOSIT CREDITING (webhook-driven, real payment only) ──
async def _credit_deposit_by_id(dep_object_id, notify: bool = True, delete_ui: bool = True) -> bool:
    """Atomically flips a pending deposit (by Mongo _id) to approved and credits the
    user. Returns False if it was already processed (prevents double-credit)."""
    result = await deposits_col.update_one(
        {"_id": dep_object_id, "status": "pending"},
        {"$set": {"status": "approved", "approved_at": datetime.utcnow(), "approved_via": "razorpay"}}
    )
    if result.modified_count == 0:
        return False
    dep = await deposits_col.find_one({"_id": dep_object_id})
    uid = dep["user_id"]
    amount = dep["amount"]
    credits = dep.get("credits", 0)
    await users_col.update_one({"user_id": uid}, {"$inc": {"balance": amount, "withdrawable": amount}})
    if delete_ui:
        await _cleanup_temp(uid)
        await _delete_msg(uid, dep.get("ui_msg_id"))
        user_states.pop(uid, None)
    if notify:
        try:
            await bot.send_message(
                uid, fancy(f"✅ **Payment Confirmed!**\n`{credits} Credits` Added To Your Wallet."),
                buttons=[[color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]])
        except Exception:
            pass
    await log_event(f"💰 **Deposit Credited**\n• User: `{uid}`\n• ₹{amount:.2f} → +{credits} Cr\n• Via: Razorpay (verified)")
    if dep.get("qr_code_id"):
        await razorpay_close_qr(dep["qr_code_id"])
    return True

# ─── 20. TEXT / FILE MESSAGE HANDLER ─────────────────────────────
@bot.on(events.NewMessage())
async def message_handler(event):
    text = event.message.text or ""
    if text.startswith("/"):
        return

    user_id    = event.sender_id
    state_data = user_states.get(user_id)
    if not state_data:
        return

    state = state_data.get("state") if isinstance(state_data, dict) else state_data

    if state == "vnh_search":
        user_states.pop(user_id, None)
        await event.respond(fancy(f"🔍 Results For `{text.strip()}`:"))
        class _FakeEvent:
            async def edit(self2, *a, **kw):
                return await event.respond(*a, **kw)
        await _send_vnh_country_list(_FakeEvent(), user_id, 0, text.strip())
        return

    if state == "await_deposit_screenshot":
        if not (event.message.photo or event.message.document):
            await event.respond(fancy("❌ ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴘʜᴏᴛᴏ sᴄʀᴇᴇɴsʜᴏᴛ."),
                                 buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "cancel_payment", "danger")]])
            return
        amount  = state_data.get("amount", 0)
        credits = state_data.get("credits", 0)
        txn_id  = state_data.get("txn_id", "N/A")
        dep_id  = state_data.get("deposit_id_obj")
        if dep_id:
            await deposits_col.update_one({"_id": ObjectId(dep_id)}, {"$set": {"screenshot_sent_at": datetime.utcnow()}})
        else:
            dep = await deposits_col.insert_one({
                "user_id": user_id, "amount": amount, "credits": credits,
                "method": "manual", "txn_id": txn_id, "status": "pending",
                "created_at": datetime.utcnow(),
            })
            dep_id = str(dep.inserted_id)
        try:
            photo_bytes = await event.message.download_media(bytes)
        except Exception as e:
            log.error(f"[manual deposit] download failed: {e}")
            photo_bytes = None
        for admin_id in await get_all_admin_ids():
            try:
                caption = fancy(
                    f"📸 **ɴᴇᴡ ᴅᴇᴘᴏsɪᴛ ʀᴇǫᴜᴇsᴛ**\n"
                    f"• ᴜsᴇʀ: `{user_id}`\n"
                    f"• ᴀᴍᴏᴜɴᴛ: `₹{amount:.2f}`\n"
                    f"• ᴄʀᴇᴅɪᴛs: `+{credits}`\n"
                    f"• ᴛxɴ ʀᴇғ: `{txn_id}`"
                )
                buttons = [[
                    color_btn("✅ ᴀᴘᴘʀᴏᴠᴇ", f"dep_approve:{dep_id}:{user_id}:{amount}", "success"),
                    color_btn("❌ ʀᴇᴊᴇᴄᴛ", f"dep_reject:{dep_id}:{user_id}", "danger"),
                ]]
                if photo_bytes:
                    photo_io = io.BytesIO(photo_bytes)
                    photo_io.name = "payment_proof.jpg"
                    await bot.send_file(admin_id, photo_io, caption=caption, buttons=buttons)
                else:
                    await bot.send_message(admin_id, caption, buttons=buttons)
            except Exception as e:
                log.warning(f"[manual deposit] could not notify admin {admin_id}: {e}")
        user_states.pop(user_id, None)
        await _cleanup_temp(user_id)
        await _delete_msg(user_id, state_data.get("ui_msg_id"))
        await event.respond(
            fancy(f"✅ **sᴄʀᴇᴇɴsʜᴏᴛ ʀᴇᴄᴇɪᴠᴇᴅ!**\n\nᴀᴅᴍɪɴ ᴡɪʟʟ ᴠᴇʀɪғʏ ʏᴏᴜʀ ₹{amount:.2f} ᴅᴇᴘᴏsɪᴛ sʜᴏʀᴛʟʏ."),
            buttons=[[color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]]
        )
        return

    if state == "custom_deposit":
        try:
            amount = float(text.strip().replace(",", ""))
        except ValueError:
            await event.respond(fancy("❌ ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))
            return
        min_d = float(await get_setting("min_deposit", 10.0))
        if amount < min_d:
            await event.respond(fancy(f"❌ ᴍɪɴɪᴍᴜᴍ ᴅᴇᴘᴏsɪᴛ ɪs ₹{min_d:.0f}."))
            return
        credits = int(amount // 1)
        await _delete_msg(user_id, state_data.get("prompt_id"))
        try:
            await event.delete()
        except Exception:
            pass
        user_states[user_id] = {"custom_amount": amount, "custom_credits": credits}
        await event.respond(
            fancy(
                "💳 **Select Payment Method**\n\n"
                f"💰 Amount: `₹{amount:.2f}` → `+{credits} Cr`\n\n"
                "⚡ **UPI / QR Auto** — Instant, No Waiting.\n"
                "📸 **UPI / QR Manual** — Screenshot, Admin Approves."
            ),
            buttons=[
                [color_btn(f"⚡ {fancy('upi / qr')} – {fancy('auto')}", "pay_method_custom:auto", "success")],
                [color_btn(f"📸 {fancy('upi / qr')} – {fancy('manual')}", "pay_method_custom:manual", "primary")],
                [color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")]
            ]
        )

    elif state == "waiting_zip" and not event.message.file:
        if not await is_admin(user_id):
            user_states.pop(user_id, None)
            return
        twofa = text.strip()
        user_states[user_id] = {**state_data, "twofa_password": twofa}
        await event.respond(
            fancy(f"🔐 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ sᴀᴠᴇᴅ: `{twofa}`\n\nɴᴏᴡ sᴇɴᴅ ᴛʜᴇ **.ᴢɪᴘ** ᴏʀ **.sᴇssɪᴏɴ** ꜰɪʟᴇ."),
            buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "admin", "danger")]],
        )

    elif state == "waiting_zip" and (event.message.file or event.message.media):
        if not await is_admin(user_id):
            user_states.pop(user_id, None)
            return
        prog = await event.respond(fancy("⏳ ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ ꜰɪʟᴇ…"))
        try:
            file_bytes = await event.message.download_media(bytes)
        except Exception as e:
            await prog.edit(f"❌ ᴅᴏᴡɴʟᴏᴀᴅ ꜰᴀɪʟᴇᴅ: {e}")
            return

        if event.message.file and event.message.file.name and event.message.file.name.lower().endswith('.session'):
            ss = await _session_file_to_string(file_bytes)
            if not ss:
                await prog.edit("❌ Invalid session file.")
                user_states.pop(user_id, None)
                return
            phone = _phone_from_filename(event.message.file.name)
            flag = COUNTRY_FLAGS.get(state_data["country_code"], "🌍")
            await accounts_col.insert_one({
                "phone":          phone,
                "session_string": ss,
                "country":        state_data["country_name"],
                "country_code":   state_data["country_code"],
                "country_flag":   flag,
                "price":          state_data["price"],
                "category":       state_data["category"],
                "server":         state_data.get("server", 1),
                "year":           state_data.get("year"),
                "twofa_password": state_data.get("twofa_password", await get_setting("default_2fa", "")),
                "status":         "available",
                "added_at":       datetime.utcnow(),
            })
            if acc_mgr is not None:
                with contextlib.suppress(Exception):
                    await acc_mgr.add_client(phone, ss)
            await prog.edit(fancy(f"✅ **ꜰɪʟᴇ ᴜᴘʟᴏᴀᴅᴇᴅ**\n`{phone}` ᴀᴅᴅᴇᴅ ᴛᴏ {state_data['country_name']}."))
            user_states.pop(user_id, None)
            return

        try:
            zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        except Exception as e:
            await prog.edit(f"❌ Invalid ZIP: {e}")
            user_states.pop(user_id, None)
            return

        twofa = state_data.get("twofa_password", "")
        try:
            info = zf.getinfo("2fa.txt")
            twofa = zf.read(info).decode().strip()
        except KeyError:
            pass
        if not twofa:
            twofa = await get_setting("default_2fa", "")

        all_names = [n for n in zf.namelist() if n.lower().endswith(".session")]
        if not all_names:
            await prog.edit("❌ No .session files found in ZIP.")
            user_states.pop(user_id, None)
            return

        total = len(all_names)
        sem = asyncio.Semaphore(5)
        added = 0
        skipped = 0
        errors = []

        async def process_one(name):
            nonlocal added, skipped
            phone = _phone_from_filename(name)
            try:
                raw_bytes = zf.read(name)
            except Exception:
                errors.append(phone)
                skipped += 1
                return
            ss = await _session_file_to_string(raw_bytes)
            if not ss:
                errors.append(phone)
                skipped += 1
                return
            flag = COUNTRY_FLAGS.get(state_data["country_code"], "🌍")
            await accounts_col.insert_one({
                "phone":          phone,
                "session_string": ss,
                "country":        state_data["country_name"],
                "country_code":   state_data["country_code"],
                "country_flag":   flag,
                "price":          state_data["price"],
                "category":       state_data["category"],
                "server":         state_data.get("server", 1),
                "year":           state_data.get("year"),
                "twofa_password": twofa,
                "status":         "available",
                "added_at":       datetime.utcnow(),
            })
            if acc_mgr is not None:
                with contextlib.suppress(Exception):
                    await acc_mgr.add_client(phone, ss)
            added += 1

        tasks = [process_one(n) for n in all_names]
        await asyncio.gather(*tasks)

        result = fancy(f"📦 **ᴜᴘʟᴏᴀᴅ ᴄᴏᴍᴘʟᴇᴛᴇ**\n\n✅ ᴀᴅᴅᴇᴅ: `{added}`\n⏭️ sᴋɪᴘᴘᴇᴅ: `{skipped}`\n")
        if errors:
            shown = errors[:5]
            result += f"❌ ꜰᴀɪʟᴇᴅ ({len(errors)}): `{', '.join(shown)}`"
        await prog.edit(result, buttons=[[color_btn("◀️ ᴀᴅᴍɪɴ", "admin", "default")]])
        user_states.pop(user_id, None)
        zf.close()

    elif state == "broadcast":
        user_states.pop(user_id, None)
        prog  = await event.respond(fancy("📢 ʙʀᴏᴀᴅᴄᴀsᴛɪɴɢ…"))
        count = 0
        fail  = 0
        async for u in users_col.find({}, {"user_id": 1}):
            try:
                await bot.send_message(u["user_id"], text)
                count += 1
            except Exception:
                fail += 1
            if (count + fail) % 50 == 0:
                try:
                    await prog.edit(f"📢 ʙʀᴏᴀᴅᴄᴀsᴛɪɴɢ… {count} sᴇɴᴛ, {fail} ꜰᴀɪʟᴇᴅ")
                except Exception:
                    pass
            await asyncio.sleep(0.05)
        await prog.edit(fancy(f"✅ ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴏᴍᴘʟᴇᴛᴇ — {count} sᴇɴᴛ, {fail} ꜰᴀɪʟᴇᴅ."))

    elif state == "setting_botname":
        await set_setting("bot_name", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ʙᴏᴛ ɴᴀᴍᴇ ᴜᴘᴅᴀᴛᴇᴅ."),
                            buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "setting_upi":
        val = text.strip()
        if val.lower() in ("remove", "clear", "none", "-", "delete"):
            await set_setting("upi_id", "")
            user_states.pop(user_id, None)
            await event.respond(fancy("✅ ᴜᴘɪ ɪᴅ ᴄʟᴇᴀʀᴇᴅ. Manual Deposits Are Disabled Until You Set A New One."),
                                buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])
            return
        if "@" not in val or " " in val or len(val) < 5:
            await event.respond(fancy(
                "❌ That Doesn't Look Like A Valid UPI ID (Should Look Like `name@bank`).\n"
                "Send A Valid UPI ID, Or Send `remove` To Clear It."
            ))
            return
        await set_setting("upi_id", val)
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ᴜᴘɪ ɪᴅ ᴜᴘᴅᴀᴛᴇᴅ."),
                            buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "setting_upiname":
        await set_setting("upi_name", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ᴜᴘɪ ɴᴀᴍᴇ ᴜᴘᴅᴀᴛᴇᴅ."),
                            buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "setting_support":
        await set_setting("support_link", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ sᴜᴘᴘᴏʀᴛ ʟɪɴᴋ ᴜᴘᴅᴀᴛᴇᴅ."),
                            buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "setting_ref_bonus":
        try:
            val = float(text.strip())
            await set_setting("referral_bonus", val)
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ ʀᴇꜰᴇʀʀᴀʟ ʙᴏɴᴜs sᴇᴛ ᴛᴏ ₹{val:.0f}."),
                                buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))

    elif state == "setting_ref_pct":
        try:
            val = float(text.strip())
            await set_setting("referral_percent", val)
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ ʀᴇꜰᴇʀʀᴀʟ % sᴇᴛ ᴛᴏ {val:.1f}%."),
                                buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))

    elif state == "setting_min_dep":
        try:
            val = float(text.strip())
            await set_setting("min_deposit", val)
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ ᴍɪɴɪᴍᴜᴍ ᴅᴇᴘᴏsɪᴛ sᴇᴛ ᴛᴏ ₹{val:.0f}."),
                                buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))

    elif state == "setting_server_count":
        try:
            val = int(text.strip())
            if val < 1:
                raise ValueError
            await set_setting("server_count", val)
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ sᴇʀᴠᴇʀ ᴄᴏᴜɴᴛ sᴇᴛ ᴛᴏ {val}."),
                                buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ (1 ᴏʀ ᴍᴏʀᴇ)."))

    elif state == "setting_vip_discount":
        try:
            val = float(text.strip())
            if not 0 <= val <= 100:
                raise ValueError
        except ValueError:
            await event.respond(fancy("❌ Send A Number Between 0 And 100."))
            return
        await set_setting("vip_discount_percent", val)
        user_states.pop(user_id, None)
        await event.respond(fancy(f"✅ VIP Discount Set To {val}%."),
                            buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "setting_vnh_markup":
        try:
            val = float(text.strip())
            if val < 0:
                raise ValueError
        except ValueError:
            await event.respond(fancy("❌ Send A Valid Non-Negative Number, E.g. `20`."))
            return
        await vnh_server.set_markup_percent(val)
        user_states.pop(user_id, None)
        await event.respond(fancy(f"✅ VNH Markup Set To {val}%."),
                            buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "setting_toggle_server":
        try:
            srv, onoff = [p.strip() for p in text.split("|", 1)]
            srv_n = int(srv)
            on = onoff.lower() in ("on", "yes", "true", "1", "enable", "enabled", "active")
        except Exception:
            await event.respond(fancy("❌ Wrong Format. Use:\n`Server | on` Or `Server | off`"))
            return
        cur = await get_setting("server_active", None)
        cur = cur if isinstance(cur, dict) else {}
        cur[str(srv_n)] = on
        await set_setting("server_active", cur)
        user_states.pop(user_id, None)
        await event.respond(fancy(f"✅ Server {srv_n} Is Now {'Active ✅' if on else 'Inactive ❌'}."),
                            buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "setting_server_labels":
        labels = {}
        for line in text.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[0].isdigit() and parts[1] and parts[2]:
                bullets = [b.strip() for b in parts[2].split(";") if b.strip()]
                if bullets:
                    labels[parts[0]] = {"tag": parts[1], "bullets": bullets}
        if not labels:
            await event.respond(fancy(
                "❌ Wrong Format. Use:\n"
                "`1 | Private Stock | Cheapest option; Directly managed`\n"
                "`2 | External Server | Third-party stock; Live rates`"
            ))
            return
        cur = await get_setting("server_labels", None)
        cur = cur if isinstance(cur, dict) else {}
        cur.update(labels)
        await set_setting("server_labels", cur)
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ Server Labels Saved."), buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "setting_year_cfg":
        try:
            parts = [p.strip() for p in text.split("|")]
            srv = int(parts[0])
            enabled = parts[1].lower() in ("on", "yes", "true", "1", "enable", "enabled")
            years = None
            if len(parts) > 2 and parts[2]:
                years = [y.strip() for y in parts[2].split(",") if y.strip()]
            kwargs = {"enabled": enabled}
            if years:
                kwargs["years"] = years
            await _set_server_year_cfg(srv, **kwargs)
            user_states.pop(user_id, None)
            await event.respond(
                fancy(f"✅ Server {srv} Year Step: {'ON' if enabled else 'OFF'}" + (f" (`{', '.join(years)}`)" if years else "")),
                buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]]
            )
        except Exception:
            await event.respond(fancy("❌ Wrong Format. Use:\n`Server | on/off | Year1,Year2,...`"))

    elif state == "setting_per_row":
        try:
            val = int(text.strip())
            if not 1 <= val <= 4:
                raise ValueError
        except ValueError:
            await event.respond(fancy("❌ Send A Number From 1 To 4."))
            return
        await set_setting("buttons_per_row", val)
        user_states.pop(user_id, None)
        await event.respond(fancy(f"✅ Buttons Per Row Set To {val}."), buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "setting_logs_channel":
        chat_id = None
        if event.message.forward and event.message.forward.chat_id:
            chat_id = event.message.forward.chat_id
        else:
            try:
                chat_id = int(text.strip())
            except ValueError:
                pass
        if not chat_id:
            await event.respond(fancy("❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴅᴇᴛᴇᴄᴛ ᴀ ᴄʜᴀɴɴᴇʟ ɪᴅ. ꜰᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ꜰʀᴏᴍ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ, ᴏʀ sᴇɴᴅ ᴛʜᴇ ɪᴅ ᴅɪʀᴇᴄᴛʟʏ."))
            return
        try:
            await bot.send_message(chat_id, fancy("✅ ᴛʜɪs ᴄʜᴀɴɴᴇʟ ɪs ɴᴏᴡ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴀs ᴛʜᴇ ʟᴏɢs ᴄʜᴀɴɴᴇʟ."))
        except Exception as e:
            await event.respond(fancy(f"❌ ᴄᴏᴜʟᴅ ɴᴏᴛ sᴇɴᴅ ᴛᴏ ᴛʜᴀᴛ ᴄʜᴀᴛ — ɪs ᴛʜᴇ ʙᴏᴛ ᴀᴅᴅᴇᴅ ᴀs ᴀᴅᴍɪɴ ᴛʜᴇʀᴇ? ({e})"))
            return
        await set_setting("logs_channel_id", chat_id)
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ʟᴏɢs ᴄʜᴀɴɴᴇʟ sᴀᴠᴇᴅ."),
                            buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "setting_default_2fa":
        await set_setting("default_2fa", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ sᴇᴛ."),
                            buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])

    elif state == "welcome_photo_set":
        if event.message.photo:
            if hasattr(event.message.media, 'photo') and hasattr(event.message.media.photo, 'id'):
                file_id = str(event.message.media.photo.id)
                await set_setting("welcome_photo", file_id)
                user_states.pop(user_id, None)
                await event.respond(fancy("🖼️ ᴡᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ sᴇᴛ!"),
                                    buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])
            else:
                await event.respond(fancy("❌ ᴜɴᴀʙʟᴇ ᴛᴏ ɢᴇᴛ ᴘʜᴏᴛᴏ ɪᴅ."))
        else:
            await event.respond(fancy("❌ ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴘʜᴏᴛᴏ."))

    elif state == "add_country":
        try:
            parts = [p.strip() for p in text.strip().split("|")]
            code, name, flag_, price_str = (parts[0].upper(), parts[1], parts[2], parts[3])
            price_val = float(price_str)
            existing  = await countries_col.find_one({"code": code})
            if existing:
                await countries_col.update_one({"code": code}, {"$set": {"name": name, "flag": flag_, "price": price_val, "is_active": True}})
                msg = f"♻️ **{name}** ᴜᴘᴅᴀᴛᴇᴅ (₹{price_val:.0f})."
            else:
                await countries_col.insert_one({"code": code, "name": name, "flag": flag_, "price": price_val, "is_active": True})
                msg = f"✅ **{name}** ᴀᴅᴅᴇᴅ (₹{price_val:.0f})."
            user_states.pop(user_id, None)
            await event.respond(fancy(msg), buttons=[[color_btn("◀️ ᴄᴏᴜɴᴛʀɪᴇs", "acountries", "default")]])
        except Exception:
            await event.respond(fancy("❌ ᴡʀᴏɴɢ ꜰᴏʀᴍᴀᴛ. ᴜsᴇ:\n`CODE | Name | Flag | Price`"))

    elif state == "add_category":
        try:
            parts = [p.strip() for p in text.strip().split("|")]
            name, icon = parts[0], parts[1]
            order_val = int(parts[2]) if len(parts) > 2 and parts[2].strip().lstrip("-").isdigit() else None
            existing = await categories_col.find_one({"name": name})
            if existing:
                upd = {"icon": icon, "is_active": True}
                if order_val is not None:
                    upd["order"] = order_val
                await categories_col.update_one({"name": name}, {"$set": upd})
                msg = f"♻️ **{name}** ᴜᴘᴅᴀᴛᴇᴅ."
            else:
                if order_val is None:
                    order_val = await categories_col.count_documents({}) + 1
                await categories_col.insert_one({"name": name, "icon": icon, "is_active": True, "order": order_val})
                msg = f"✅ **{name}** ᴀᴅᴅᴇᴅ (ᴏʀᴅᴇʀ {order_val})."
            user_states.pop(user_id, None)
            await event.respond(fancy(msg), buttons=[[color_btn("◀️ ᴄᴀᴛᴇɢᴏʀɪᴇs", "acategories", "default")]])
        except Exception:
            await event.respond(fancy("❌ ᴡʀᴏɴɢ ꜰᴏʀᴍᴀᴛ. ᴜsᴇ:\n`Name | Icon | Order(optional)`"))

    elif state == "search_user":
        try:
            tid    = int(text.strip())
            target = await users_col.find_one({"user_id": tid})
            if not target:
                await event.respond(fancy("❌ ᴜsᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ."))
                return
            o_count = await orders_col.count_documents({"user_id": tid})
            d_count = await deposits_col.count_documents({"user_id": tid})
            banned_ = "🚫 ʏᴇs" if target.get("is_banned") else "✅ ɴᴏ"
            vip_ = "⭐ ʏᴇs" if target.get("is_vip") else "ɴᴏ"
            await event.respond(
                fancy(f"👤 **ᴜsᴇʀ ɪɴꜰᴏ**\n\n• ɪᴅ: `{tid}`\n• ʙᴀʟᴀɴᴄᴇ: `₹{target.get('balance', 0):.2f}`\n• ᴏʀᴅᴇʀs: `{o_count}`\n• ᴅᴇᴘᴏsɪᴛs: `{d_count}`\n• ʙᴀɴɴᴇᴅ: {banned_}\n• ᴠɪᴘ: {vip_}\n• ᴊᴏɪɴᴇᴅ: {target.get('joined_at','?')}"),
                buttons=[[color_btn(
                    "⭐ ʀᴇᴠᴏᴋᴇ ᴠɪᴘ" if target.get("is_vip") else "⭐ ᴍᴀᴋᴇ ᴠɪᴘ",
                    f"vip_toggle:{tid}", "primary"
                )]]
            )
            user_states.pop(user_id, None)
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ."))

    elif state == "add_bal_uid":
        try:
            tid = int(text.strip())
            user_states[user_id] = {"state": "add_bal_amount", "target_id": tid}
            await event.respond(fancy(f"💰 ʜᴏᴡ ᴍᴜᴄʜ ᴛᴏ ᴀᴅᴅ ғᴏʀ ᴜsᴇʀ `{tid}`? (₹)"))
        except ValueError:
            await event.respond(fancy("❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ."))

    elif state == "add_bal_amount":
        try:
            amount  = float(text.strip())
            tid     = state_data["target_id"]
            await users_col.update_one({"user_id": tid}, {"$inc": {"balance": amount, "withdrawable": amount}})
            try:
                await bot.send_message(tid, fancy(f"💰 **₹{amount:.0f} ᴀᴅᴅᴇᴅ** ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ ʙʏ ᴀᴅᴍɪɴ!"))
            except Exception:
                pass
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ ₹{amount:.0f} ᴀᴅᴅᴇᴅ ᴛᴏ ᴜsᴇʀ `{tid}`."),
                                buttons=[[color_btn("◀️ ᴀᴅᴍɪɴ", "admin", "default")]])
        except ValueError:
            await event.respond(fancy("❌ ɪɴᴠᴀʟɪᴅ ᴀᴍᴏᴜɴᴛ."))

    elif state == "ban_uid":
        try:
            tid    = int(text.strip())
            target = await users_col.find_one({"user_id": tid})
            if not target:
                await event.respond(fancy("❌ ᴜsᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ."))
                user_states.pop(user_id, None)
                return
            new_ban = not target.get("is_banned", False)
            await users_col.update_one({"user_id": tid}, {"$set": {"is_banned": new_ban}})
            action = "ʙᴀɴɴᴇᴅ" if new_ban else "ᴜɴʙᴀɴɴᴇᴅ"
            try:
                await bot.send_message(tid, fancy("🚫 ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ **ʙᴀɴɴᴇᴅ** ғʀᴏᴍ ᴛʜɪs ʙᴏᴛ." if new_ban else "✅ ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ **ᴜɴʙᴀɴɴᴇᴅ**. ᴡᴇʟᴄᴏᴍᴇ ʙᴀᴄᴋ!"))
            except Exception:
                pass
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ ᴜsᴇʀ `{tid}` ʜᴀs ʙᴇᴇɴ **{action}**."),
                                buttons=[[color_btn("◀️ ᴀᴅᴍɪɴ", "admin", "default")]])
        except ValueError:
            await event.respond(fancy("❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ."))

    elif state == "add_admin":
        try:
            new_id = int(text.strip())
            try:
                entity = await bot.get_entity(new_id)
                name_  = getattr(entity, "first_name", str(new_id))
                uname_ = getattr(entity, "username", None)
            except Exception:
                name_  = str(new_id)
                uname_ = None
            existing = await bot_admins_col.find_one({"telegram_id": new_id})
            if existing:
                await bot_admins_col.update_one({"telegram_id": new_id}, {"$set": {"is_active": True}})
            else:
                await bot_admins_col.insert_one({"telegram_id": new_id, "name": name_, "username": uname_, "is_active": True, "added_by": user_id, "added_at": datetime.utcnow()})
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ **ᴀᴅᴍɪɴ ᴀᴅᴅᴇᴅ:** {name_} (`{new_id}`)"),
                                buttons=[[color_btn("◀️ ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs", "manage_admins", "default")]])
            try:
                await bot.send_message(new_id, fancy("🔑 ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ ɢʀᴀɴᴛᴇᴅ **ᴀᴅᴍɪɴ ᴀᴄᴄᴇss** ᴛᴏ ᴛʜᴇ ʙᴏᴛ!"))
            except Exception:
                pass
        except ValueError:
            await event.respond(fancy("❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ."))

# ─── 21. (removed duplicate deposit_paid handler — already handled by the
#          global CallbackQuery router above; a second registration here
#          was firing callback_router() twice per tap, causing errors) ──

# ─── 22. COMMANDS ──────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern="/help"))
async def cmd_help(event):
    await event.respond(
        fancy("❓ **Help Center**\n\nChoose A Topic Below:"),
        buttons=[
            [color_btn("📖 ʜᴏᴡ ᴛᴏ ᴜsᴇ", "help_howto", "default")],
            [color_btn("🔑 ᴏᴛᴘ ʜᴇʟᴘ", "help_otp", "default")],
            [color_btn("💳 ᴘᴀʏᴍᴇɴᴛ ʜᴇʟᴘ", "help_payment", "default")],
            [color_btn("🛡️ ᴡʜʏ ᴛʀᴜsᴛ ᴜs?", "help_trust", "default")],
            [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")],
        ])

@bot.on(events.NewMessage(pattern="/ping"))
async def cmd_ping(event):
    await event.respond(fancy("🏓 **ᴘᴏɴɢ!**\nʙᴏᴛ ɪs ᴀʟɪᴠᴇ ᴀɴᴅ ʀᴜɴɴɪɴɢ."))

@bot.on(events.NewMessage(pattern="/cancel"))
async def cmd_cancel(event):
    user_states.pop(event.sender_id, None)
    await event.respond(fancy("✅ ᴄᴜʀʀᴇɴᴛ ᴀᴄᴛɪᴏɴ ᴄᴀɴᴄᴇʟʟᴇᴅ."),
                        buttons=[[color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]])

@bot.on(events.NewMessage(pattern="/info"))
async def cmd_info(event):
    args = event.message.text.split()
    if len(args) < 2:
        await event.respond(fancy("❌ ᴜsᴀɢᴇ: `/info <ᴜsᴇʀ_ɪᴅ ᴏʀ ᴏʀᴅᴇʀ_ɪᴅ>`"))
        return
    target = args[1]
    try:
        uid = int(target)
        user = await users_col.find_one({"user_id": uid})
        if user:
            orders = await orders_col.count_documents({"user_id": uid})
            deps = await deposits_col.count_documents({"user_id": uid})
            await event.respond(fancy(f"👤 **ᴜsᴇʀ ɪɴꜰᴏ**\n\n• ɪᴅ: `{uid}`\n• ʙᴀʟᴀɴᴄᴇ: `₹{user.get('balance',0):.2f}`\n• ᴏʀᴅᴇʀs: `{orders}`\n• ᴅᴇᴘᴏsɪᴛs: `{deps}`"))
            return
    except ValueError:
        pass
    order = await orders_col.find_one({"_id": target})
    if order:
        await event.respond(fancy(f"📋 **ᴏʀᴅᴇʀ ɪɴꜰᴏ**\n\n• ɪᴅ: `{target}`\n• ᴜsᴇʀ: `{order.get('user_id')}`\n• ᴄᴏᴜɴᴛʀʏ: {order.get('country_flag','')} {order.get('country')}\n• ᴘʜᴏɴᴇ: `{order.get('phone')}`\n• sᴛᴀᴛᴜs: `{order.get('status')}`"))
    else:
        await event.respond(fancy("❌ ɴᴏ ᴜsᴇʀ ᴏʀ ᴏʀᴅᴇʀ ꜰᴏᴜɴᴅ."))

@bot.on(events.NewMessage(pattern="/request"))
async def cmd_request(event):
    args = event.message.text.split()
    if len(args) < 2:
        await event.respond(fancy("❌ ᴜsᴀɢᴇ: `/request <ᴄᴏᴜɴᴛʀʏ_ᴄᴏᴅᴇ>`"))
        return
    cc = args[1].upper()
    for admin_id in await get_all_admin_ids():
        try:
            await bot.send_message(admin_id, fancy(f"📢 **ʀᴇǫᴜᴇsᴛ ғᴏʀ ʀᴇsᴛᴏᴄᴋ**\n\nᴜsᴇʀ `{event.sender_id}` ʀᴇǫᴜᴇsᴛs `{cc}`."))
        except Exception:
            pass
    await event.respond(fancy(f"✅ ʀᴇǫᴜᴇsᴛ ғᴏʀ `{cc}` sᴇɴᴛ ᴛᴏ ᴀᴅᴍɪɴs."))

@bot.on(events.NewMessage(pattern="/feedback"))
async def cmd_feedback(event):
    args = event.message.text.split(maxsplit=1)
    if len(args) < 2:
        await event.respond(fancy("❌ ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ʏᴏᴜʀ ꜰᴇᴇᴅʙᴀᴄᴋ ᴀғᴛᴇʀ ᴛʜᴇ ᴄᴏᴍᴍᴀɴᴅ."))
        return
    feedback = args[1]
    for admin_id in await get_all_admin_ids():
        try:
            await bot.send_message(admin_id, fancy(f"💬 **ɴᴇᴡ ꜰᴇᴇᴅʙᴀᴄᴋ**\n\nᴜsᴇʀ `{event.sender_id}`:\n{feedback}"))
        except Exception:
            pass
    await event.respond(fancy("✅ ᴛʜᴀɴᴋ ʏᴏᴜ ғᴏʀ ʏᴏᴜʀ ꜰᴇᴇᴅʙᴀᴄᴋ!"))

# ─── 23. SELF‑PING (only needed on hosts that sleep an idle web
#          service, like Render's free tier; harmless no-op on AWS
#          EC2/ECS/App Runner where the process just keeps running) ──
async def self_ping():
    while True:
        await asyncio.sleep(240)
        url = RENDER_EXTERNAL_URL
        if not url:
            continue
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                await session.get(f"{url}/ping", timeout=10)
                log.info("[self-ping] Ping sent to keep bot awake.")
        except Exception as e:
            log.warning(f"[self-ping] Failed: {e}")

# ─── 24. HEALTH‑CHECK WEB SERVER ──────────────────────────────
import threading
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_web = FastAPI(title="Prime Vault", docs_url="/docs")

@_web.get("/", response_class=JSONResponse)
async def health():
    return {"status": "ok", "bot": "Prime Vault is running"}

@_web.get("/ping")
async def ping():
    return {"pong": True}

# ─── 24b. UPI APP REDIRECT (opens the phone's UPI app) ──────────
from fastapi.responses import HTMLResponse
import json as _json

@_web.get("/pay/{token}")
async def pay_redirect(token: str):
    doc = await db["pay_links"].find_one({"token": token})
    if not doc or not str(doc.get("link", "")).startswith("upi://"):
        return HTMLResponse("<h3 style='font-family:sans-serif;text-align:center;margin-top:40px'>"
                            "Link expired. Go back to the bot and try again.</h3>", status_code=404)
    link = doc["link"]
    js_link = _json.dumps(link).replace("</", "<\\/")
    page = (
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Pay</title><body style='font-family:sans-serif;text-align:center;padding:40px'>"
        "<h3>Opening your UPI app…</h3><p>If nothing happens, tap the button:</p>"
        f"<p><a href='{_html.escape(link, quote=True)}' style='display:inline-block;padding:14px 26px;"
        "background:#2e7d32;color:#fff;border-radius:10px;text-decoration:none;font-size:18px'>Open UPI App</a></p>"
        f"<script>setTimeout(function(){{location.href={js_link};}},300);</script></body>"
    )
    return HTMLResponse(page)

# ─── 25. RAZORPAY WEBHOOK (real, signature-verified) ────────────
@_web.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not RAZORPAY_WEBHOOK_SECRET or not signature:
        log.warning("[Razorpay webhook] rejected — webhook secret not configured or no signature header")
        return {"status": "webhook not configured"}

    expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        log.warning("[Razorpay webhook] rejected — signature mismatch")
        return {"status": "invalid signature"}

    try:
        body = json.loads(raw_body)
    except Exception:
        return {"status": "bad json"}

    event_name = body.get("event")

    if event_name == "qr_code.credited":
        try:
            qr_entity = body["payload"]["qr_code"]["entity"]
            payment_entity = body["payload"]["payment"]["entity"]
        except (KeyError, TypeError):
            return {"status": "ignored"}

        qr_id = qr_entity.get("id")
        paid_amount = payment_entity.get("amount", 0) / 100  # paise -> rupees
        dep = await deposits_col.find_one({"qr_code_id": qr_id, "status": "pending"})
        if not dep:
            log.warning(f"[Razorpay webhook] no pending deposit for qr_code_id={qr_id}")
            return {"status": "no matching deposit"}
        if round(paid_amount, 2) != round(dep.get("amount", 0), 2):
            log.warning(f"[Razorpay webhook] amount mismatch for {qr_id}: paid {paid_amount} vs expected {dep.get('amount')}")
            await log_event(f"⚠️ **ᴀᴍᴏᴜɴᴛ ᴍɪsᴍᴀᴛᴄʜ** ᴏɴ Qʀ `{qr_id}` — ᴘᴀɪᴅ ₹{paid_amount}, ᴇxᴘᴇᴄᴛᴇᴅ ₹{dep.get('amount')}. Nᴏᴛ ᴀᴜᴛᴏ-ᴄʀᴇᴅɪᴛᴇᴅ.")
            return {"status": "amount mismatch — manual review needed"}
        await _credit_deposit_by_id(dep["_id"])
        return {"status": "approved"}

    return {"status": "ok"}

def _start_health_server() -> None:
    port = int(os.getenv("PORT", 8080))
    log.info(f"[health] FastAPI listening on port {port}")
    uvicorn.run(_web, host="0.0.0.0", port=port, log_level="warning")

# ─── 26. MAIN ────────────────────────────────────────────────────
async def main():
    threading.Thread(target=_start_health_server, daemon=True).start()
    await init_db()
    await bot.start(bot_token=BOT_TOKEN)

    global acc_mgr
    acc_mgr = AccountManager(accounts_col, bot, API_ID, API_HASH, pending_otp_requests)
    await acc_mgr.load_all()

    if RENDER_EXTERNAL_URL:
        asyncio.create_task(self_ping())

    bot_name = await get_setting("bot_name", "Prime Vault")
    log.info(f"🚀 {bot_name} is running…")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
