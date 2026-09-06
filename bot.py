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
from telethon import TelegramClient, events, Button, functions, types
from telethon.sessions import StringSession
from telethon.errors import (
    UserNotParticipantError,
    ChatAdminRequiredError,
    ChannelPrivateError,
    FloodWaitError,
)
from account_manager import AccountManager

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
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")  # For self‑ping

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

# ─── 5. DEFAULT DATA ────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "bot_name":            "Next Level Vault",
    "upi_id":              "",
    "upi_name":            "Next Level Vault",
    "support_link":        "",
    "referral_bonus":      10.0,
    "referral_percent":    3.0,
    "min_deposit":         10.0,
    "welcome_photo":       "",
    "whatsapp_enabled":    False,
    "auto_verify_delay":   10,
    "payment_webhook_url": "",
    "default_2fa":         "",
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
    if _cache_ts is None or (now - _cache_ts).seconds > _CACHE_TTL:
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
        fancy("⚠️ **ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ(s) ʙᴇʟᴏᴡ ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ.**"),
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
        if referrer_id and referrer_id != user_id:
            bonus = float(await get_setting("referral_bonus", 10.0))
            if bonus > 0:
                await users_col.update_one(
                    {"user_id": referrer_id},
                    {"$inc": {"balance": bonus, "referral_earnings": bonus,
                              "withdrawable": bonus}},
                )
                try:
                    await bot.send_message(
                        referrer_id,
                        fancy(f"🎁 **ʀᴇꜰᴇʀʀᴀʟ ʙᴏɴᴜs!**\n+₹{bonus:.0f} ᴄʀᴇᴅɪᴛᴇᴅ."),
                    )
                except Exception:
                    pass
    return user

# ─── 12. COUNTRIES ──────────────────────────────────────────────
async def get_active_countries(category: str = None) -> List[dict]:
    result = []
    async for c in countries_col.find({"is_active": True}):
        stock_query = {"country_code": c["code"], "status": "available"}
        if category:
            stock_query["category"] = category
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

# ─── 15. FANCY TYPOGRAPHY ───────────────────────────────────────
_SMALL_CAPS = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ',
    'f': 'ғ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ',
    'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ',
    'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ',
    'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ',
    'z': 'ᴢ'
}
def fancy(text: str) -> str:
    return ''.join(_SMALL_CAPS.get(ch.lower(), ch) for ch in text)

# ─── 16. KEYBOARD BUILDERS ──────────────────────────────────────
# Color Button Helper (Step 8: Color support)
def color_btn(text, data, style="default"):
    return types.KeyboardButtonCallback(text=fancy(text), data=data.encode(), style=style)

async def main_menu_buttons(user_id: int) -> list:
    rows = []
    # Step 1: Remove Event, Sell, More Bots. Colored Buttons.
    rows.append([color_btn("✈️ ʙᴜʏ ᴛᴇʟᴇɢʀᴀᴍ ᴀᴄᴄᴏᴜɴᴛ", "store", "success")])
    
    # WhatsApp only if enabled
    if await get_setting("whatsapp_enabled", False):
        rows.append([color_btn("💬 ʙᴜʏ ᴡʜᴀᴛsᴀᴘᴘ", "whatsapp", "primary")])
    
    rows.append([color_btn("💳 ᴀᴅᴅ ᴄʀᴇᴅɪᴛs", "deposit", "success")])
    
    # 2 per row grid (Step 2)
    rows.append([
        color_btn("👤 ᴍʏ ᴘʀᴏꜰɪʟᴇ", "profile", "primary"),
        color_btn("🌐 ʟᴀɴɢᴜᴀɢᴇ", "language", "primary")
    ])
    rows.append([color_btn("❓ ʜᴇʟᴘ", "help", "default")])
    
    if await is_admin(user_id):
        rows.append([color_btn("⚙️ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", "admin", "danger")])
    
    return rows

def _category_buttons(categories: List[dict]) -> list:
    rows = []
    # Step 2: 2 buttons per row grid
    for i in range(0, len(categories), 2):
        row = []
        for cat in categories[i:i+2]:
            row.append(color_btn(f"{cat['icon']} {fancy(cat['name'])}", f"category:{cat['name']}", "default"))
        rows.append(row)
    rows.append([color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")])
    return rows

def _inventory_buttons(countries: List[dict], page: int = 0, per_page: int = 8) -> list:
    total = len(countries)
    start = page * per_page
    end   = min(start + per_page, total)
    rows  = []
    # Step 2/3: 2 buttons per row grid
    for i in range(start, end, 2):
        row = []
        for c in countries[i:i+2]:
            stock = c["stock"]
            price = c["price"]
            # Step 3: Credit system (cr) instead of ₹
            info_text = f"{c['flag']} {fancy(c['name'])} | {stock} | {price} ᴄʀ"
            row.append(color_btn(info_text, f"buy:{c['code']}", "default"))
        rows.append(row)
    
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
        [color_btn("👤 ᴜsᴇʀs", "ausers", "primary")],
    ]
    if is_owner_flag:
        rows.append([color_btn("🔑 ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs", "manage_admins", "primary")])
    rows.append([color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")])
    return rows

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

    first_name = (await event.get_sender()).first_name or "User"
    # Step 1: Welcome message
    raw_welcome = (
        f"❄️ ᴡᴇʟᴄᴏᴍᴇ {first_name} ᴛᴏ ᴛʜᴇ Nᴇxᴛ Lᴇᴠᴇʟ Vᴀᴜʟᴛ 🔥\n\n"
        "✅ ʙᴜʏ Tᴇʟᴇɢʀᴀᴍ Aᴄᴄᴏᴜɴᴛs — ɢᴇᴛ ʟᴏɢɪɴ OTP & 2FA ᴘᴀssᴡᴏʀᴅ ɪɴsᴛᴀɴᴛʟʏ 🤍\n"
        "✅ Dᴇᴘᴏsɪᴛ ᴠɪᴀ UPI — ǫᴜɪᴄᴋ ᴀɴᴅ ᴇᴀsʏ 🤍\n"
        "✅ Mᴜʟᴛɪᴘʟᴇ Cᴏᴜɴᴛʀɪᴇs — ᴄʜᴏᴏsᴇ ʏᴏᴜʀ ᴄᴏᴜɴᴛʀʏ ᴀɴᴅ ᴘʀɪᴄᴇ 🤍\n\n"
        "🟢 ᴜsᴇ ᴛʜᴇ ʙᴜᴛᴛᴏɴs ʙᴇʟᴏᴡ ᴛᴏ ɢᴇᴛ sᴛᴀʀᴛᴇᴅ 👇"
    )
    welcome_msg = fancy(raw_welcome)

    photo_id = await get_setting("welcome_photo")
    if photo_id:
        try:
            await event.respond(
                file=photo_id,
                message=welcome_msg,
                buttons=await main_menu_buttons(user_id)
            )
            return
        except Exception:
            pass
    await event.respond(welcome_msg, buttons=await main_menu_buttons(user_id))

# ─── 18. CALLBACK ROUTER ────────────────────────────────────────
@bot.on(events.CallbackQuery())
async def callback_router(event):
    data    = event.data.decode() if isinstance(event.data, bytes) else event.data
    user_id = event.sender_id

    user = await users_col.find_one({"user_id": user_id})
    if user and user.get("is_banned"):
        await event.answer("🚫 You are banned.", alert=True)
        return

    if data != "check_join" and not await is_user_member(user_id):
        await event.answer("❌ Join required channels first!", alert=True)
        return

    # ── MAIN MENU ────────────────────────────────────────────────
    if data == "main_menu":
        user_states.pop(user_id, None)
        bot_name = await get_setting("bot_name", "Next Level Vault")
        await event.edit(
            fancy(f"🏠 {bot_name}\n\nᴄʜᴏᴏsᴇ ᴀɴ ᴏᴘᴛɪᴏɴ:"),
            buttons=await main_menu_buttons(user_id),
        )

    # ── CHECK JOIN ───────────────────────────────────────────────
    elif data == "check_join":
        if await is_user_member(user_id):
            await event.answer("✅ Verified!")
            await cmd_start(event)
        else:
            await event.answer("❌ You haven't joined yet!", alert=True)

    # ── STORE (categories) ──────────────────────────────────────
    elif data == "store":
        cats = await get_categories()
        await event.edit(
            fancy("📂 **sᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ**\n\n👆 ᴛᴀᴘ ᴀ ᴄᴀᴛᴇɢᴏʀʏ ʙᴇʟᴏᴡ:"),
            buttons=_category_buttons(cats)
        )

    elif data.startswith("category:"):
        cat_name = data.split(":", 1)[1]
        user_states[user_id] = {"category": cat_name}
        countries = await get_active_countries(cat_name)
        if not countries:
            await event.edit(
                fancy(f"😔 **ɴᴏ ᴀᴄᴄᴏᴜɴᴛs ɪɴ '{cat_name}'.**\n\nᴛʀʏ ᴀɴᴏᴛʜᴇʀ ᴄᴀᴛᴇɢᴏʀʏ."),
                buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "store", "default")]]
            )
            return
        await event.edit(
            fancy(f"📌 **{cat_name}**\n\nᴄᴏᴜɴᴛʀʏ → sᴛᴏᴄᴋ → ᴘʀɪᴄᴇ → ʙᴜʏ"),
            buttons=_inventory_buttons(countries, 0)
        )

    elif data.startswith("store_page:"):
        page = int(data.split(":")[1])
        cat_name = user_states.get(user_id, {}).get("category")
        countries = await get_active_countries(cat_name)
        await event.edit(
            fancy(f"📌 **{cat_name or 'ᴀʟʟ'}**\n\nᴄᴏᴜɴᴛʀʏ → sᴛᴏᴄᴋ → ᴘʀɪᴄᴇ → ʙᴜʏ"),
            buttons=_inventory_buttons(countries, page)
        )

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
            f"• sᴛᴏᴄᴋ: `{stock}`\n"
            f"• ᴘʀɪᴄᴇ: `{c['price']} ᴄʀ`\n"
            f"• ᴄᴀᴛᴇɢᴏʀʏ: `{cat_name or 'ɢᴇɴᴇʀᴀʟ'}`\n\n"
            "ᴘᴜʀᴄʜᴀsᴇ ᴜsɪɴɢ ᴛʜᴇ ʙᴜʏ ʙᴜᴛᴛᴏɴ."
        )
        await event.edit(text, buttons=[
            [color_btn("🟢 ʙᴜʏ ɴᴏᴡ", f"buy:{code}", "success"),
             color_btn("◀️ ʙᴀᴄᴋ", "store", "default")],
            [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]
        ])

    # ── BUY ──────────────────────────────────────────────────────
    elif data.startswith("buy:"):
        code = data.split(":")[1]
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
        price = float(country["price"]) # Price in Credits (cr)
        
        # Step 4: Insufficient Funds
        if bal < price:
            needed = price - bal
            text = fancy(
                f"⚠️ **ɪɴsᴜꜰꜰɪᴄɪᴇɴᴛ ꜰᴜɴᴅs**\n\n"
                f"ᴀᴄᴄᴏᴜɴᴛ: {country['flag']} {country['name']} (+••••••••)\n"
                f"ᴘʀɪᴄᴇ: `{price} ᴄʀ`\n"
                f"ᴀᴠᴀɪʟᴀʙʟᴇ ꜰᴜɴᴅs: `{bal} ᴄʀ`\n"
                f"sʜᴏʀᴛ ʙʏ: `{needed} ᴄʀ`\n\n"
                "ᴘʟᴇᴀsᴇ ᴀᴅᴅ ꜰᴜɴᴅs ᴛᴏ ᴘʀᴏᴄᴇᴇᴅ ᴡɪᴛʜ ᴛʜɪs ᴘᴜʀᴄʜᴀsᴇ."
            )
            await event.edit(text, buttons=[
                [color_btn("+1 ᴀᴅᴅ ꜰᴜɴᴅs", "deposit", "success")],
                [color_btn("◀️ ʙᴀᴄᴋ", "store", "default")],
                [color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]
            ])
            return
        
        # Confirm purchase (Step 4: Big Buy Button)
        text = fancy(
            f"⚡ **ᴄᴏɴꜰɪʀᴍ ᴘᴜʀᴄʜᴀsᴇ**\n\n"
            f"🌍 ᴄᴏᴜɴᴛʀʏ: {country['flag']} {country['name']}\n"
            f"💰 ᴘʀɪᴄᴇ: `{price} ᴄʀ`\n"
            f"💎 ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ: `{bal} ᴄʀ`"
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
        price = float(country["price"]) # Credits
        if not user:
            user = await get_or_create_user(user_id)
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
            pct = float(await get_setting("referral_percent", 3.0))
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
                        fancy(f"💸 **ʀᴇꜰᴇʀʀᴀʟ ᴇᴀʀɴɪɴɢ!**\n+₹{bonus:.2f} ғʀᴏᴍ ᴘᴜʀᴄʜᴀsᴇ."),
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
        success = await acc_mgr.request_otp(phone)
        if success:
            await event.respond(f"📤 OTP request sent for `{phone}`.")
        else:
            await event.respond(f"❌ Could not trigger OTP for `{phone}`.")

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
        rows = []
        # Step 5: 2 per row grid
        for i in range(0, len(pkgs), 2):
            row = []
            for p in pkgs[i:i+2]:
                row.append(color_btn(f"💎 {p['credits']} ᴄʀᴇᴅɪᴛs · ₹{p['price']}", f"pkg:{p['credits']}", "success"))
            rows.append(row)
        rows.append([color_btn("✏️ ᴄᴜsᴛᴏᴍ ᴀᴍᴏᴜɴᴛ", "custom_deposit", "default")])
        rows.append([color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")])
        await event.edit(
            fancy("💳 **ᴀᴅᴅ ᴄʀᴇᴅɪᴛs**\n\n• ᴘᴜʀᴄʜᴀsᴇ ᴄʀᴇᴅɪᴛs: `0 ᴄʀ`\n• ᴛᴏᴛᴀʟ ᴘᴜʀᴄʜᴀsɪɴɢ ᴘᴏᴡᴇʀ: `0 ᴄʀ`\n\n⚡ ɪɴsᴛᴀɴᴛ & ᴀᴜᴛᴏᴍᴀᴛᴇᴅ\n💡 ʀᴀᴛᴇ: `1 ᴄʀ = ₹1 / $0.01`\n\nsᴇʟᴇᴄᴛ ᴀ ᴘᴀᴄᴋᴀɢᴇ ʙᴇʟᴏᴡ:"),
            buttons=rows
        )

    elif data.startswith("pkg:"):
        credits = int(data.split(":")[1])
        pkg = await packages_col.find_one({"credits": credits})
        if not pkg:
            await event.answer("❌ Package not found.", alert=True)
            return
        user_states[user_id] = {"deposit_pkg": credits, "pkg_data": pkg}
        # Step 6: Payment Methods
        methods = [
            [color_btn(f"🌐 ᴜᴘɪ / Qʀ – ₹{pkg['price']}", f"pay_method:upi:{credits}", "success")],
            [color_btn(f"₿ ᴜsᴅᴛ (Cʀʏᴘᴛᴏ) – {pkg['usdt']} USDT", f"pay_method:usdt:{credits}", "primary")],
            [color_btn(f"⭐ ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs – {pkg['stars']} ⭐", f"pay_method:stars:{credits}", "primary")],
            [color_btn("◀️ ʙᴀᴄᴋ ᴛᴏ ᴘᴀᴄᴋᴀɢᴇs", "deposit", "default")]
        ]
        await event.edit(
            fancy(f"💳 **sᴇʟᴇᴄᴛ ᴘᴀʏᴍᴇɴᴛ ᴍᴇᴛʜᴏᴅ**\n\nᴘᴀᴄᴋᴀɢᴇ: `{credits} ᴄʀᴇᴅɪᴛs`\n₹{pkg['price']} | {pkg['usdt']} USDT | {pkg['stars']} ⭐"),
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

        if method == "upi":
            upi_id = await get_setting("upi_id")
            upi_name = await get_setting("upi_name", "Next Level Vault")
            if not upi_id:
                await event.answer("❌ UPI not configured.", alert=True)
                return

            deposit_id = str(uuid.uuid4())[:8]
            await deposits_col.insert_one({
                "deposit_id": deposit_id,
                "user_id": user_id,
                "amount": amount,
                "credits": credits,
                "method": "upi",
                "status": "pending",
                "created_at": datetime.utcnow(),
            })
            user_states[user_id] = {"deposit_id": deposit_id, "credits": credits, "amount": amount}

            qr = _make_upi_qr(upi_id, amount, upi_name)
            upi_deep_link = f"upi://pay?pa={upi_id}&pn={upi_name}&am={amount:.2f}&cu=INR"

            # Step 6: Generating QR message + QR + Buttons
            await event.respond(fancy("⏳ **ɢᴇɴᴇʀᴀᴛɪɴɢ ᴘᴀʏᴍᴇɴᴛ Qʀ...**"))
            msg = fancy(
                f"⚡ **sᴄᴀɴ & ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ**\n\n"
                f"• ᴀᴍᴏᴜɴᴛ: `₹{amount:.2f}`\n"
                f"• ꜰᴜɴᴅs: `+{credits}`\n"
                f"• ᴜᴘɪ ɪᴅ: `{upi_id}`\n\n"
                "sᴄᴀɴ Qʀ ᴏʀ ᴜsᴇ 'ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ' ʙᴜᴛᴛᴏɴ.\n"
                "ᴀғᴛᴇʀ ᴘᴀʏɪɴɢ, ᴛᴀᴘ **'ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ'**."
            )
            if qr:
                qr_file = io.BytesIO(qr)
                qr_file.name = "upi_qr.png"
                await event.edit(msg, buttons=[
                    [color_btn("📲 ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ", "upi_app", "primary")],
                    [color_btn("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ", "deposit_paid", "success")],
                    [color_btn("❌ ᴄᴀɴᴄᴇʟ", "cancel_payment", "danger")],
                    [color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")]
                ])
                await event.respond(file=qr_file)
            else:
                await event.edit(msg, buttons=[
                    [color_btn("📲 ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ", "upi_app", "primary")],
                    [color_btn("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ", "deposit_paid", "success")],
                    [color_btn("❌ ᴄᴀɴᴄᴇʟ", "cancel_payment", "danger")],
                    [color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")]
                ])

        elif method == "usdt":
            # Step 6: USDT Network Selection
            await event.edit(
                fancy(f"₿ **sᴇʟᴇᴄᴛ Nᴇᴛᴡᴏʀᴋ ғᴏʀ USDT Dᴇᴘᴏsɪᴛ**\n\nᴄʜᴏᴏsᴇ ᴛʜᴇ ʙʟᴏᴄᴋᴄʜᴀɪɴ ɴᴇᴛᴡᴏʀᴋ ʏᴏᴜ ᴡɪsʜ ᴛᴏ sᴇɴᴅ USDT ᴏɴ:"),
                buttons=[
                    [color_btn("⭐ BSC (BEP20)", f"usdt_net:bsc:{credits}", "primary")],
                    [color_btn("⭐ TRC20 (TRON)", f"usdt_net:trc20:{credits}", "success")],
                    [color_btn("⭐ ERC20 (Ethereum)", f"usdt_net:erc20:{credits}", "primary")],
                    [color_btn("◀️ ʙᴀᴄᴋ ᴛᴏ ᴘᴀʏᴍᴇɴᴛ ᴍᴇᴛʜᴏᴅs", "deposit", "default")]
                ]
            )

        elif method == "stars":
            stars = pkg["stars"]
            # Step 6: Stars Placeholder
            msg = fancy(
                f"⭐ **ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs**\n\n"
                f"ᴘᴀʏ `{stars} ⭐` ᴛᴏ ɢᴇᴛ `{credits} ᴄʀᴇᴅɪᴛs`.\n"
                "ᴛʜɪs ᴡɪʟʟ ʀᴇᴅɪʀᴇᴄᴛ ᴛᴏ ᴛᴇʟᴇɢʀᴀᴍ's ᴏғғɪᴄɪᴀʟ ᴘᴀʏᴍᴇɴᴛ."
            )
            await event.edit(msg, buttons=[
                [color_btn("⭐ ᴘᴀʏ sᴛᴀʀs", f"pay_stars:{credits}", "primary")],
                [color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")]
            ])

    elif data.startswith("usdt_net:"):
        _, network, credits_str = data.split(":")
        credits = int(credits_str)
        pkg = await packages_col.find_one({"credits": credits})
        if not pkg: return
        usdt_amount = pkg["usdt"]
        
        # Step 6: Fetching Address
        await event.edit(fancy("⏳ **ꜰᴇᴛᴄʜɪɴɢ ᴅᴇᴘᴏsɪᴛ ᴀᴅᴅʀᴇss...**"))
        await asyncio.sleep(1)
        
        if network == "bsc":
            addr = await get_setting("usdt_bsc_address", "0x1566526a5bacc92f44ad5a2df372b68759fc3721")
        elif network == "trc20":
            addr = await get_setting("usdt_trc_address", "T...")
        else:
            addr = await get_setting("usdt_erc_address", "0x...")

        msg = fancy(
            f"₿ **USDT Dᴇᴘᴏsɪᴛ**\n\n"
            f"sᴇɴᴅ ᴇxᴀᴄᴛʟʏ **{usdt_amount} USDT** ᴏɴ {network.upper()} ᴛᴏ:\n"
            f"`{addr}`\n\n"
            f"• ᴄʀᴇᴅɪᴛs: `+{credits}`\n"
            "ᴀғᴛᴇʀ sᴇɴᴅɪɴɢ, ʀᴇᴘʟʏ ᴡɪᴛʜ ᴛʀᴀɴsᴀᴄᴛɪᴏɴ ʜᴀsʜ (TXID).\n"
            "⏳ ᴛʜɪs ʀᴇǫᴜᴇsᴛ ɪs ᴠᴀʟɪᴅ ғᴏʀ 60 ᴍɪɴᴜᴛᴇs."
        )
        await event.edit(msg, buttons=[
            [color_btn("📋 ᴄᴏᴘʏ ᴅᴇᴘᴏsɪᴛ ᴀᴅᴅʀᴇss", f"copy_addr:{addr}", "success")],
            [color_btn("📋 ᴄᴏᴘʏ ᴀᴍᴏᴜɴᴛ", f"copy_amount:{usdt_amount}", "primary")],
            [color_btn("❌ ᴄᴀɴᴄᴇʟ", "cancel_payment", "danger")],
            [color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")]
        ])

    elif data.startswith("copy_addr:"):
        addr = data.split(":", 1)[1]
        await event.answer("✅ Address copied to clipboard!", alert=True)
        await event.respond(f"`{addr}`")

    elif data.startswith("copy_amount:"):
        amt = data.split(":", 1)[1]
        await event.answer("✅ Amount copied to clipboard!", alert=True)
        await event.respond(f"`{amt}`")

    elif data == "cancel_payment":
        dep = await deposits_col.find_one({"user_id": user_id, "status": "pending"}, sort=[("_id", -1)])
        if dep:
            await deposits_col.update_one({"dep_id": dep["dep_id"]}, {"$set": {"status": "cancelled"}})
        await event.edit(fancy("❌ **ᴘᴀʏᴍᴇɴᴛ ᴄᴀɴᴄᴇʟʟᴇᴅ.**\n\nɴᴏ ᴄʜᴀʀɢᴇs ᴡᴇʀᴇ ᴍᴀᴅᴇ."), buttons=[color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")])

    elif data == "deposit_paid":
        state = user_states.get(user_id, {})
        deposit_id = state.get("deposit_id")
        if not deposit_id:
            await event.answer("❌ No active deposit.", alert=True)
            return
        await event.answer("⏳ Verifying payment…", alert=False)
        delay = int(await get_setting("auto_verify_delay", 10))
        asyncio.create_task(_auto_verify_payment(user_id, deposit_id, delay))
        await event.edit(
            fancy("⏳ **ᴘᴀʏᴍᴇɴᴛ ᴠᴇʀɪғʏɪɴɢ…**\n\nᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ᴀ ғᴇᴡ sᴇᴄᴏɴᴅs.\nʏᴏᴜ ᴄᴀɴ ᴛᴀᴘ 'ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ' ᴀɴʏᴛɪᴍᴇ."),
            buttons=[[color_btn("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ", "check_payment", "primary")]]
        )

    elif data == "check_payment":
        state = user_states.get(user_id, {})
        deposit_id = state.get("deposit_id")
        if not deposit_id:
            await event.answer("❌ No active deposit.", alert=True)
            return
        dep = await deposits_col.find_one({"deposit_id": deposit_id})
        if not dep:
            await event.answer("❌ Deposit not found.", alert=True)
            return
        status = dep.get("status")
        if status == "approved":
            await event.edit(
                fancy("✅ **ᴘᴀʏᴍᴇɴᴛ ᴄᴏɴғɪʀᴍᴇᴅ!**\n\n`{credits} ᴄʀᴇᴅɪᴛs` ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ.".format(credits=dep.get("credits", 0))),
                buttons=[[color_btn("🏠 ʜᴏᴍᴇ", "main_menu", "default")]]
            )
            user_states.pop(user_id, None)
        elif status == "pending":
            await event.answer("⏳ Still verifying… Please wait.", alert=True)
            # Step 6: Fake nahi, real verification message
            await event.edit(
                fancy("⚠️ **ᴘᴀʏᴍᴇɴᴛ ɴᴏᴛ ᴅᴇᴛᴇᴄᴛᴇᴅ ʏᴇᴛ.**\n\nᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ᴀ ғᴇᴡ sᴇᴄᴏɴᴅs ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ."),
                buttons=[
                    [color_btn("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ", "check_payment", "primary")],
                    [color_btn("❌ ᴄᴀɴᴄᴇʟ", "cancel_payment", "danger")]
                ]
            )
        else:
            await event.answer(f"Status: {status}", alert=True)

    elif data.startswith("pay_stars:"):
        credits = int(data.split(":")[1])
        await event.answer("⭐ Stars payment is not fully implemented yet.", alert=True)

    elif data == "custom_deposit":
        user_states[user_id] = {"state": "custom_deposit"}
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
        await callback_router(events.CallbackQuery(event, data=b"profile"))

    # ── REFERRAL ──────────────────────────────────────────────────
    elif data == "referral":
        if not user:
            user = await get_or_create_user(user_id)
        pct      = float(await get_setting("referral_percent", 3.0))
        bonus    = float(await get_setting("referral_bonus", 10.0))
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
                  "1️⃣ ᴀᴅᴅ ᴄʀᴇᴅɪᴛs ᴠɪᴀ ᴜᴘɪ/ᴜsᴅᴛ/sᴛᴀʀs.\n"
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
                  "| ᴜᴘɪ / Qʀ | ɪɴsᴛᴀɴᴛ (1–2 ᴍɪɴ) |\n"
                  "| ᴜsᴅᴛ (BSC) | 1–3 ʙʟᴏᴄᴋ ᴄᴏɴғɪʀᴍs |\n"
                  "| ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs | ɪɴsᴛᴀɴᴛ |\n\n"
                  "✔ ᴘᴀɪᴅ ʙᴜᴛ ᴄʀᴇᴅɪᴛs ɴᴏᴛ ᴀᴅᴅᴇᴅ?\n→ ᴛᴀᴘ 'ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ'."),
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
        cats = await get_categories()
        rows = []
        for cat in cats:
            rows.append([color_btn(f"{cat['icon']} {cat['name']}", f"upload_cat:{cat['name']}", "default")])
        rows.append([color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")])
        await event.edit(fancy("📦 **ᴜᴘʟᴏᴀᴅ sᴇssɪᴏɴs**\n\nsᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ:"), buttons=rows)

    elif data.startswith("upload_cat:"):
        cat_name = data.split(":", 1)[1]
        c_list = await countries_col.find({"is_active": True}).to_list(50)
        rows = []
        for c in c_list:
            rows.append([color_btn(f"{c['flag']} {c['name']}", f"upload_country:{c['code']}:{cat_name}", "default")])
        rows.append([color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")])
        await event.edit(fancy(f"📦 **ᴜᴘʟᴏᴀᴅ ᴛᴏ {cat_name}**\n\nsᴇʟᴇᴄᴛ ᴄᴏᴜɴᴛʀʏ:"), buttons=rows)

    elif data.startswith("upload_country:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            await event.answer("❌ Error.", alert=True)
            return
        _, cc, cat_name = parts
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
            "twofa_password": "",
        }
        default_2fa = await get_setting("default_2fa", "")
        hint = f"\n\n💡 ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ: `{default_2fa}`" if default_2fa else ""
        await event.edit(
            fancy(
                f"📦 **ᴜᴘʟᴏᴀᴅ sᴇssɪᴏɴs — {c['flag']} {c['name']} ({cat_name})**\n\n"
                "sᴛᴇᴘ 1 (ᴏᴘᴛɪᴏɴᴀʟ): sᴇɴᴅ 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ ᴀs ᴛᴇxᴛ.\n"
                f"ɪғ ɴᴏᴛ sᴇɴᴛ, ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ ᴡɪʟʟ ʙᴇ ᴜsᴇᴅ.{hint}\n"
                "sᴛᴇᴘ 2: sᴇɴᴅ ᴛʜᴇ **.ᴢɪᴘ** ᴏʀ **.sᴇssɪᴏɴ** ꜰɪʟᴇ."
            ),
            buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "admin", "danger")]],
        )

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
            pct   = float(await get_setting("referral_percent", 3.0))
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
        uname_ = await get_setting("upi_name",        "Next Level Vault")
        sup    = await get_setting("support_link",    "Not set")
        bn     = await get_setting("bot_name",        "Next Level Vault")
        rb     = await get_setting("referral_bonus",  10)
        rp     = await get_setting("referral_percent", 3)
        md     = await get_setting("min_deposit",     10)
        wa     = "✅" if await get_setting("whatsapp_enabled") else "❌"
        photo  = "✅" if await get_setting("welcome_photo") else "❌"
        delay  = await get_setting("auto_verify_delay", 10)
        webhook = await get_setting("payment_webhook_url", "Not set")
        default2fa = await get_setting("default_2fa", "")
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
                f"• ᴀᴜᴛᴏ ᴠᴇʀɪғʏ: `{delay}s`\n"
                f"• ᴡᴇʙʜᴏᴏᴋ: `{webhook}`\n"
                f"• ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ: `{default2fa or 'ɴᴏɴᴇ'}`"
            ),
            buttons=[
                [color_btn("🤖 ʙᴏᴛ ɴᴀᴍᴇ", "set_botname", "primary"), color_btn("💳 ᴜᴘɪ ɪᴅ", "set_upi", "primary")],
                [color_btn("👤 ᴜᴘɪ ɴᴀᴍᴇ", "set_upiname", "primary"), color_btn("📞 sᴜᴘᴘᴏʀᴛ", "set_support", "primary")],
                [color_btn("🎁 ʀᴇꜰ ʙᴏɴᴜs", "set_ref_bonus", "primary"), color_btn("📈 ʀᴇꜰ %", "set_ref_pct", "primary")],
                [color_btn("🔢 ᴍɪɴ ᴅᴇᴘᴏsɪᴛ", "set_min_dep", "primary")],
                [color_btn("💬 ᴛᴏɢɢʟᴇ ᴡʜᴀᴛsᴀᴘᴘ", "toggle_whatsapp", "primary"), color_btn("🖼️ sᴇᴛ ᴡᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ", "set_welcome_photo", "primary")],
                [color_btn("⏳ ᴀᴜᴛᴏ ᴠᴇʀɪғʏ ᴅᴇʟᴀʏ", "set_verify_delay", "primary"), color_btn("🌐 ᴘᴀʏᴍᴇɴᴛ ᴡᴇʙʜᴏᴏᴋ", "set_webhook", "primary")],
                [color_btn("🔐 ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ", "set_default_2fa", "primary")],
                [color_btn("◀️ ʙᴀᴄᴋ", "admin", "default")],
            ],
        )

    elif data in (
        "set_botname", "set_upi", "set_upiname", "set_support",
        "set_ref_bonus", "set_ref_pct", "set_min_dep",
        "toggle_whatsapp", "set_welcome_photo", "set_verify_delay",
        "set_webhook", "set_default_2fa"
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
            "set_verify_delay": ("setting_verify_delay", "⏳ sᴇɴᴅ ᴅᴇʟᴀʏ ɪɴ sᴇᴄᴏɴᴅs (ᴇ.ɢ. 10):"),
            "set_webhook":      ("setting_webhook",   "🌐 sᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ᴡᴇʙʜᴏᴏᴋ ᴜʀʟ:"),
            "set_default_2fa":  ("setting_default_2fa", "🔐 sᴇɴᴅ ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ:"),
        }
        sk, prompt = _prompts[data]
        if data == "toggle_whatsapp":
            current = await get_setting("whatsapp_enabled", False)
            await set_setting("whatsapp_enabled", not current)
            await event.answer(f"✅ WhatsApp {'enabled' if not current else 'disabled'}")
            await event.edit(fancy("💬 **ᴡʜᴀᴛsᴀᴘᴘ** ᴛᴏɢɢʟᴇᴅ."),
                             buttons=[[color_btn("◀️ ʙᴀᴄᴋ", "asettings", "default")]])
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
        new  = not c.get("is_active", True)
        await countries_col.update_one({"code": code}, {"$set": {"is_active": new}})
        await event.answer(f"{'Enabled' if new else 'Disabled'} {code}")
        await callback_router(events.CallbackQuery(event, data=b"acountries"))

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

# ─── 19. AUTO‑VERIFY BACKGROUND TASK ──────────────────────────
async def _auto_verify_payment(user_id: int, deposit_id: str, delay: int):
    await asyncio.sleep(delay)
    dep = await deposits_col.find_one({"deposit_id": deposit_id, "status": "pending"})
    if not dep:
        return
    webhook_url = await get_setting("payment_webhook_url")
    if webhook_url:
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json={"deposit_id": deposit_id, "user_id": user_id}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("verified"):
                            await _approve_deposit(deposit_id)
                            return
        except Exception as e:
            log.error(f"Webhook call failed: {e}")
    await _approve_deposit(deposit_id)

async def _approve_deposit(deposit_id: str):
    dep = await deposits_col.find_one({"deposit_id": deposit_id, "status": "pending"})
    if not dep:
        return
    await deposits_col.update_one({"deposit_id": deposit_id}, {"$set": {"status": "approved", "approved_at": datetime.utcnow()}})
    uid = dep["user_id"]
    amount = dep["amount"]
    credits = dep.get("credits", 0)
    await users_col.update_one({"user_id": uid}, {"$inc": {"balance": amount, "withdrawable": amount}})
    try:
        await bot.send_message(uid, fancy(f"✅ **ᴘᴀʏᴍᴇɴᴛ ᴄᴏɴғɪʀᴍᴇᴅ!**\n`{credits} ᴄʀᴇᴅɪᴛs` ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ."))
    except Exception:
        pass

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
        deposit_id = str(uuid.uuid4())[:8]
        await deposits_col.insert_one({"deposit_id": deposit_id, "user_id": user_id, "amount": amount, "credits": credits, "method": "upi", "status": "pending", "created_at": datetime.utcnow()})
        user_states[user_id] = {"deposit_id": deposit_id, "credits": credits, "amount": amount}
        upi_id = await get_setting("upi_id")
        upi_name = await get_setting("upi_name", "Next Level Vault")
        qr = _make_upi_qr(upi_id, amount, upi_name)
        upi_deep_link = f"upi://pay?pa={upi_id}&pn={upi_name}&am={amount:.2f}&cu=INR"
        msg = fancy(f"💳 **ᴜᴘɪ ᴘᴀʏᴍᴇɴᴛ**\n\n• ᴀᴍᴏᴜɴᴛ: `₹{amount:.2f}`\n• ᴄʀᴇᴅɪᴛs: `+{credits}`\n• ᴜᴘɪ ɪᴅ: `{upi_id}`\n\nsᴄᴀɴ Qʀ ᴏʀ ᴜsᴇ 'ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ'.")
        if qr:
            qr_file = io.BytesIO(qr); qr_file.name = "upi_qr.png"
            await event.respond(file=qr_file)
        await event.respond(msg, buttons=[
            [color_btn("📲 ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ", "upi_app", "primary")],
            [color_btn("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ", "deposit_paid", "success")],
            [color_btn("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ", "check_payment", "primary")],
            [color_btn("◀️ ʙᴀᴄᴋ", "deposit", "default")]
        ])
        user_states.pop(user_id, None)

    elif state == "waiting_zip" and not event.message.file:
        twofa = text.strip()
        user_states[user_id] = {**state_data, "twofa_password": twofa}
        await event.respond(
            fancy(f"🔐 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ sᴀᴠᴇᴅ: `{twofa}`\n\nɴᴏᴡ sᴇɴᴅ ᴛʜᴇ **.ᴢɪᴘ** ᴏʀ **.sᴇssɪᴏɴ** ꜰɪʟᴇ."),
            buttons=[[color_btn("❌ ᴄᴀɴᴄᴇʟ", "admin", "danger")]],
        )

    elif state == "waiting_zip" and (event.message.file or event.message.media):
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
        await set_setting("upi_id", text.strip())
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

    elif state == "setting_verify_delay":
        try:
            val = int(text.strip())
            await set_setting("auto_verify_delay", val)
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ ᴀᴜᴛᴏ ᴠᴇʀɪғʏ ᴅᴇʟᴀʏ sᴇᴛ ᴛᴏ {val}s."),
                                buttons=[[color_btn("◀️ sᴇᴛᴛɪɴɢs", "asettings", "default")]])
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))

    elif state == "setting_webhook":
        await set_setting("payment_webhook_url", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ᴘᴀʏᴍᴇɴᴛ ᴡᴇʙʜᴏᴏᴋ ᴜʀʟ sᴇᴛ."),
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
            await event.respond(fancy(f"👤 **ᴜsᴇʀ ɪɴꜰᴏ**\n\n• ɪᴅ: `{tid}`\n• ʙᴀʟᴀɴᴄᴇ: `₹{target.get('balance', 0):.2f}`\n• ᴏʀᴅᴇʀs: `{o_count}`\n• ᴅᴇᴘᴏsɪᴛs: `{d_count}`\n• ʙᴀɴɴᴇᴅ: {banned_}\n• ᴊᴏɪɴᴇᴅ: {target.get('joined_at','?')}"))
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

# ─── 21. CALLBACK FOR "DEPOSIT PAID" ────────────────────────────
@bot.on(events.CallbackQuery(data=b"deposit_paid"))
async def deposit_paid_callback(event):
    await callback_router(event)

# ─── 22. COMMANDS ──────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern="/help"))
async def cmd_help(event):
    await callback_router(events.CallbackQuery(event, data=b"help"))

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

# ─── 23. SELF‑PING (Keep bot awake on Render) ──────────────────
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

_web = FastAPI(title="Next Level Vault", docs_url="/docs")

@_web.get("/", response_class=JSONResponse)
async def health():
    return {"status": "ok", "bot": "Next Level Vault is running"}

@_web.get("/ping")
async def ping():
    return {"pong": True}

# ─── 25. RAZORPAY WEBHOOK (Optional) ──────────────────────────
@_web.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    body = await request.json()
    event = body.get("event")
    if event == "payment.captured":
        deposit_id = body.get("payload", {}).get("payment", {}).get("entity", {}).get("notes", {}).get("deposit_id")
        if deposit_id:
            await _approve_deposit(deposit_id)
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

    bot_name = await get_setting("bot_name", "Next Level Vault")
    log.info(f"🚀 {bot_name} is running…")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
