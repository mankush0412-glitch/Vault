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
async def main_menu_buttons(user_id: int) -> list:
    rows = [
        [Button.inline("🛒 ʙᴜʏ ᴀᴄᴄᴏᴜɴᴛ", b"store"),
         Button.inline("💳 ᴀᴅᴅ ᴄʀᴇᴅɪᴛs", b"deposit")],
        [Button.inline("👤 ᴍʏ ᴘʀᴏꜰɪʟᴇ", b"profile"),
         Button.inline("📋 ᴍʏ ᴏʀᴅᴇʀs", b"orders")],
        [Button.inline("👥 ʀᴇꜰᴇʀʀᴀʟ", b"referral"),
         Button.inline("📜 ʜɪsᴛᴏʀʏ", b"history")],
    ]
    support = await get_setting("support_link")
    if support:
        rows.append([Button.url("📞 sᴜᴘᴘᴏʀᴛ", support)])
    else:
        rows.append([Button.inline("❓ ʜᴇʟᴘ", b"help")])
    if await get_setting("whatsapp_enabled", False):
        rows.append([Button.inline("💬 ᴡʜᴀᴛsᴀᴘᴘ", b"whatsapp")])
    if await is_admin(user_id):
        rows.append([Button.inline("⚙️ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", b"admin")])
    return rows

def _category_buttons(categories: List[dict]) -> list:
    rows = []
    for cat in categories:
        rows.append([Button.inline(
            f"{cat['icon']} {cat['name']}",
            f"category:{cat['name']}".encode()
        )])
    rows.append([Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")])
    return rows

def _inventory_buttons(countries: List[dict], page: int = 0, per_page: int = 8) -> list:
    total = len(countries)
    start = page * per_page
    end   = min(start + per_page, total)
    rows  = []
    for c in countries[start:end]:
        stock = c["stock"]
        price = c["price"]
        info_text = f"{c['flag']} {c['name']}  {stock}  ₹{price:.0f}"
        rows.append([
            Button.inline(info_text, f"detail:{c['code']}".encode()),
            Button.inline("🟢 ʙᴜʏ", f"buy:{c['code']}".encode())
        ])
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️", f"store_page:{page-1}".encode()))
    nav.append(Button.inline(f"{page+1}/{(total-1)//per_page + 1}", b"store_noop"))
    if end < total:
        nav.append(Button.inline("▶️", f"store_page:{page+1}".encode()))
    if nav:
        rows.append(nav)
    rows.append([Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")])
    return rows

def _admin_menu_buttons(is_owner_flag: bool) -> list:
    rows = [
        [Button.inline("📊 sᴛᴀᴛs", b"astats")],
        [Button.inline("📦 ᴜᴘʟᴏᴀᴅ sᴇssɪᴏɴs", b"upload_sessions"),
         Button.inline("📋 sᴇssɪᴏɴ ᴏᴠᴇʀᴠɪᴇᴡ", b"manage_sessions")],
        [Button.inline("💳 ᴘᴇɴᴅɪɴɢ ᴅᴇᴘᴏsɪᴛs", b"pending_deposits")],
        [Button.inline("📢 ʙʀᴏᴀᴅᴄᴀsᴛ", b"broadcast")],
        [Button.inline("⚙️ sᴇᴛᴛɪɴɢs", b"asettings"),
         Button.inline("🌍 ᴄᴏᴜɴᴛʀɪᴇs", b"acountries")],
        [Button.inline("👤 ᴜsᴇʀs", b"ausers")],
    ]
    if is_owner_flag:
        rows.append([Button.inline("🔑 ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs", b"manage_admins")])
    rows.append([Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")])
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
    raw_welcome = (
        f"❄️ Welcome {first_name}to the Next Level VAULT 🔥\n\n"
        "✅ Buy Telegram Accounts — get login OTP & 2FA password instantly 🤍\n"
        "✅ Deposit via UPI  — quick and easy. 🤍\n"
        "✅ Multiple Countries — choose your country and price 🤍\n\n"
        "🟢 Use the buttons below to get started 👇"
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
            fancy(f"🏠 {bot_name}\n\nChoose an option:"),
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
            fancy("📂 **sᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ**\n\nᴛᴀᴘ ᴀ ᴄᴀᴛᴇɢᴏʀʏ ʙᴇʟᴏᴡ:"),
            buttons=_category_buttons(cats)
        )

    elif data.startswith("category:"):
        cat_name = data.split(":", 1)[1]
        user_states[user_id] = {"category": cat_name}
        countries = await get_active_countries(cat_name)
        if not countries:
            await event.edit(
                fancy(f"😔 **ɴᴏ ᴀᴄᴄᴏᴜɴᴛs ɪɴ '{cat_name}'.**\n\nᴛʀʏ ᴀɴᴏᴛʜᴇʀ ᴄᴀᴛᴇɢᴏʀʏ."),
                buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"store")]]
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
        text = fancy(
            f"🌍 **{c['flag']} {c['name']}**\n\n"
            f"📦 sᴛᴏᴄᴋ: `{stock}`\n"
            f"💰 ᴘʀɪᴄᴇ: `₹{c['price']:.2f}`\n"
            f"📂 ᴄᴀᴛᴇɢᴏʀʏ: `{cat_name or 'ɢᴇɴᴇʀᴀʟ'}`\n\n"
            "ᴘᴜʀᴄʜᴀsᴇ ᴜsɪɴɢ ᴛʜᴇ ʙᴜʏ ʙᴜᴛᴛᴏɴ."
        )
        await event.edit(text, buttons=[
            [Button.inline("🟢 ʙᴜʏ ɴᴏᴡ", f"buy:{code}".encode()),
             Button.inline("◀️ ʙᴀᴄᴋ", b"store")],
            [Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]
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
        price = float(country["price"])
        if bal < price:
            needed = price - bal
            text = fancy(
                f"❌ **ɪɴsᴜꜰꜰɪᴄɪᴇɴᴛ ʙᴀʟᴀɴᴄᴇ**\n\n"
                f"ʏᴏᴜ ɴᴇᴇᴅ `₹{needed:.2f}` ᴍᴏʀᴇ.\n"
                f"ᴘʟᴇᴀsᴇ ᴀᴅᴅ ᴄʀᴇᴅɪᴛs."
            )
            await event.edit(text, buttons=[
                [Button.inline("💳 ᴀᴅᴅ ᴄʀᴇᴅɪᴛs", b"deposit")],
                [Button.inline("◀️ ʙᴀᴄᴋ", b"store")]
            ])
            return
        # Confirm purchase
        text = fancy(
            f"⚡ **ᴄᴏɴꜰɪʀᴍ ᴘᴜʀᴄʜᴀsᴇ**\n\n"
            f"🌍 ᴄᴏᴜɴᴛʀʏ: {country['flag']} {country['name']}\n"
            f"💰 ᴘʀɪᴄᴇ: `₹{price:.2f}`\n"
            f"💎 ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ: `₹{bal:.2f}`"
        )
        await event.edit(text, buttons=[
            [Button.inline("✅ ᴄᴏɴꜰɪʀᴍ", f"confirm_buy:{code}".encode()),
             Button.inline("❌ ᴄᴀɴᴄᴇʟ", b"store")],
            [Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]
        ])

    # ── CONFIRM BUY ──────────────────────────────────────────────
    elif data.startswith("confirm_buy:"):
        country_code = data.split(":")[1]
        country = await countries_col.find_one({"code": country_code, "is_active": True})
        if not country:
            await event.answer("❌ Country no longer available.", alert=True)
            return
        price = float(country["price"])
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

        msg = fancy(
            f"✅ **ᴘᴜʀᴄʜᴀsᴇ sᴜᴄᴄᴇssꜰᴜʟ!**\n\n"
            f"🌍 ᴄᴏᴜɴᴛʀʏ: {country.get('flag','')} {country['name']}\n"
            f"📞 ᴘʜᴏɴᴇ: `{phone}`\n"
            f"📂 ᴄᴀᴛᴇɢᴏʀʏ: `{cat_name}`\n"
        )
        if twofa:
            msg += f"🔐 **2FA:** `{twofa}`\n"
        msg += "\n⏳ ᴘʟᴇᴀsᴇ ᴛᴀᴘ **ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ** ᴛᴏ ɢᴇᴛ ʏᴏᴜʀ ᴄᴏᴅᴇ."
        await event.edit(msg, buttons=[
            [Button.inline("📩 ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ", f"resend_{phone}".encode())],
            [Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")],
        ])

    # ── REQUEST OTP ─────────────────────────────────────────────
    elif data.startswith("resend_"):
        phone = data[7:]
        await event.answer("⏳ Requesting OTP…", alert=False)
        success = await acc_mgr.request_otp(phone, call=False)
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
            buttons=[[Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]],
        )

    # ── MY ORDERS ──────────────────────────────────────────────
    elif data == "orders":
        docs = await orders_col.find(
            {"user_id": user_id}
        ).sort("created_at", -1).limit(8).to_list(8)
        if not docs:
            await event.edit(
                fancy("📋 **ᴍʏ ᴏʀᴅᴇʀs**\n\n_ɴᴏ ᴏʀᴅᴇʀs ʏᴇᴛ._"),
                buttons=[[Button.inline("🛒 ʙᴜʏ ɴᴏᴡ", b"store"),
                          Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]],
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
                f"📱 `{o.get('phone','?')}`  •  ₹{o.get('amount',0):.0f}"
            )
            if o.get("twofa"):
                line += f"\n🔐 2FA: `{o['twofa']}`"
            lines.append(line)
        kb = [[Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]]
        if docs[0].get("status") == "waiting_otp":
            kb.insert(0, [Button.inline(
                "📩 ʀᴇ-ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ",
                f"resend_{docs[0]['phone']}".encode(),
            )])
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
            f"💸 ᴛᴏᴛᴀʟ sᴘᴇɴᴛ:     `₹{spent:.2f}`",
            f"💰 ᴛᴏᴛᴀʟ ᴅᴇᴘᴏsɪᴛᴇᴅ: `₹{dep_tot:.2f}`",
        ]
        if orders:
            lines.append("\n🛒 **ʀᴇᴄᴇɴᴛ ᴘᴜʀᴄʜᴀsᴇs:**")
            for o in orders:
                lines.append(
                    f"• {o.get('country_flag','')} {o.get('country','?')}"
                    f" — `₹{o.get('amount',0):.0f}` — `{o.get('phone','?')}`"
                )
        if deps:
            lines.append("\n💰 **ʀᴇᴄᴇɴᴛ ᴅᴇᴘᴏsɪᴛs:**")
            _DE = {"approved": "✅", "pending": "⏳", "rejected": "❌"}
            for d in deps:
                lines.append(
                    f"{_DE.get(d.get('status',''),'•')} "
                    f"₹{d.get('amount',0):.0f} — {d.get('status','').title()}"
                )
        await event.edit(
            "\n".join(lines),
            buttons=[[Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]],
        )

    # ─── DEPOSIT – PACKAGES ──────────────────────────────────────
    elif data == "deposit":
        pkgs = []
        async for p in packages_col.find({}).sort("credits", 1):
            pkgs.append(p)
        rows = []
        for p in pkgs:
            rows.append([Button.inline(
                f"✅ {p['credits']} ᴄʀᴇᴅɪᴛs · ₹{p['price']}",
                f"pkg:{p['credits']}".encode()
            )])
        rows.append([Button.inline("💡 ᴄᴜsᴛᴏᴍ ᴀᴍᴏᴜɴᴛ", b"custom_deposit")])
        rows.append([Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")])
        await event.edit(
            fancy("💳 **ᴀᴅᴅ ᴄʀᴇᴅɪᴛs**\n\nsᴇʟᴇᴄᴛ ᴀ ᴘᴀᴄᴋᴀɢᴇ ʙᴇʟᴏᴡ:"),
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
            [Button.inline("💳 ᴜᴘɪ / Qʀ – ₹{}".format(pkg['price']), f"pay_method:upi:{credits}".encode())],
            [Button.inline("₿ ᴜsᴅᴛ (ᴄʀʏᴘᴛᴏ) – {} USDT".format(pkg['usdt']), f"pay_method:usdt:{credits}".encode())],
            [Button.inline("⭐ ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs – {} ⭐".format(pkg['stars']), f"pay_method:stars:{credits}".encode())],
            [Button.inline("◀️ ʙᴀᴄᴋ ᴛᴏ ᴘᴀᴄᴋᴀɢᴇs", b"deposit")]
        ]
        await event.edit(
            fancy(f"💳 **sᴇʟᴇᴄᴛ ᴘᴀʏᴍᴇɴᴛ ᴍᴇᴛʜᴏᴅ**\n\n"
                  f"ᴘᴀᴄᴋᴀɢᴇ: `{credits} ᴄʀᴇᴅɪᴛs`\n"
                  f"₹{pkg['price']}  |  {pkg['usdt']} USDT  |  {pkg['stars']} ⭐\n\n"
                  "ᴀʟʟ ᴘᴀʏᴍᴇɴᴛs ᴀʀᴇ ᴠᴇʀɪғɪᴇᴅ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ."),
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

            msg = fancy(
                f"💳 **sᴄᴀɴ & ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ**\n\n"
                f"ᴀᴍᴏᴜɴᴛ: `₹{amount:.2f}`\n"
                f"ᴄʀᴇᴅɪᴛs: `+{credits}`\n"
                f"ᴜᴘɪ ɪᴅ: `{upi_id}`\n\n"
                "sᴄᴀɴ Qʀ ᴏʀ ᴜsᴇ 'ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ' ʙᴜᴛᴛᴏɴ.\n"
                "ᴀғᴛᴇʀ ᴘᴀʏɪɴɢ, ᴛᴀᴘ **'ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ'**."
            )
            if qr:
                qr_file = io.BytesIO(qr)
                qr_file.name = "upi_qr.png"
                await event.edit(msg, buttons=[
                    [Button.url("📲 ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ", upi_deep_link)],
                    [Button.inline("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ", b"deposit_paid")],
                    [Button.inline("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ", b"check_payment")],
                    [Button.inline("◀️ ʙᴀᴄᴋ", b"deposit")]
                ])
                await event.respond(file=qr_file)
            else:
                await event.edit(msg, buttons=[
                    [Button.url("📲 ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ", upi_deep_link)],
                    [Button.inline("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ", b"deposit_paid")],
                    [Button.inline("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ", b"check_payment")],
                    [Button.inline("◀️ ʙᴀᴄᴋ", b"deposit")]
                ])

        elif method == "usdt":
            crypto_addr = await get_setting("usdt_address", "0x1566526a5bacc92f44ad5a2df372b68759fc3721")
            usdt_amount = pkg["usdt"]
            deposit_id = str(uuid.uuid4())[:8]
            await deposits_col.insert_one({
                "deposit_id": deposit_id,
                "user_id": user_id,
                "amount": amount,
                "credits": credits,
                "method": "usdt",
                "status": "pending",
                "created_at": datetime.utcnow(),
            })
            user_states[user_id] = {"deposit_id": deposit_id, "credits": credits, "amount": amount}
            msg = fancy(
                f"₿ **ᴜsᴅᴛ ᴅᴇᴘᴏsɪᴛ**\n\n"
                f"sᴇɴᴅ ᴇxᴀᴄᴛʟʏ **{usdt_amount} USDT** ᴏɴ BSC (BEP20) ᴛᴏ:\n"
                f"`{crypto_addr}`\n\n"
                f"ᴄʀᴇᴅɪᴛs: `+{credits}`\n"
                "ᴀғᴛᴇʀ sᴇɴᴅɪɴɢ, ᴛᴀᴘ **'ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ'**."
            )
            await event.edit(msg, buttons=[
                [Button.inline("📋 ᴄᴏᴘʏ ᴀᴅᴅʀᴇss", f"copy_addr:{crypto_addr}".encode())],
                [Button.inline("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ", b"deposit_paid")],
                [Button.inline("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ", b"check_payment")],
                [Button.inline("◀️ ʙᴀᴄᴋ", b"deposit")]
            ])

        elif method == "stars":
            stars = pkg["stars"]
            msg = fancy(
                f"⭐ **ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs**\n\n"
                f"ᴘᴀʏ `{stars} ⭐` ᴛᴏ ɢᴇᴛ `{credits} ᴄʀᴇᴅɪᴛs`.\n"
                "ᴄʟɪᴄᴋ ʙᴇʟᴏᴡ ᴛᴏ ᴘᴀʏ."
            )
            await event.edit(msg, buttons=[
                [Button.inline("⭐ ᴘᴀʏ sᴛᴀʀs", f"pay_stars:{credits}".encode())],
                [Button.inline("◀️ ʙᴀᴄᴋ", b"deposit")]
            ])

    elif data.startswith("copy_addr:"):
        addr = data.split(":", 1)[1]
        await event.answer("✅ Address copied to clipboard!", alert=True)
        await event.respond(f"`{addr}`")

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
            fancy("⏳ **ᴘᴀʏᴍᴇɴᴛ ᴠᴇʀɪғʏɪɴɢ…**\n\n"
                  "ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ᴀ ғᴇᴡ sᴇᴄᴏɴᴅs.\n"
                  "ʏᴏᴜ ᴄᴀɴ ᴛᴀᴘ 'ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ' ᴀɴʏᴛɪᴍᴇ."),
            buttons=[[Button.inline("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ", b"check_payment")]]
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
                fancy("✅ **ᴘᴀʏᴍᴇɴᴛ ᴄᴏɴғɪʀᴍᴇᴅ!**\n\n"
                      f"`{dep['credits']} ᴄʀᴇᴅɪᴛs` ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ."),
                buttons=[[Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]]
            )
            user_states.pop(user_id, None)
        elif status == "pending":
            await event.answer("⏳ Still verifying… Please wait.", alert=True)
        else:
            await event.answer(f"Status: {status}", alert=True)

    elif data.startswith("pay_stars:"):
        credits = int(data.split(":")[1])
        await event.answer("⭐ Stars payment is not fully implemented yet.", alert=True)

    elif data == "custom_deposit":
        user_states[user_id] = {"state": "custom_deposit"}
        min_dep = await get_setting("min_deposit", 10.0)
        await event.edit(
            fancy(f"💡 **ᴄᴜsᴛᴏᴍ ᴀᴍᴏᴜɴᴛ**\n\n"
                  f"ᴇɴᴛᴇʀ ᴛʜᴇ ᴀᴍᴏᴜɴᴛ ɪɴ ₹ (ᴍɪɴɪᴍᴜᴍ {min_dep}):"),
            buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"deposit")]]
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
                [Button.inline("📋 ᴏʀᴅᴇʀs", b"orders"),
                 Button.inline("📜 ʜɪsᴛᴏʀʏ", b"history")],
                [Button.inline("👥 ʀᴇꜰᴇʀʀᴀʟ", b"referral"),
                 Button.inline("🌐 ʟᴀɴɢᴜᴀɢᴇ", b"language")],
                [Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]
            ]
        )

    # ── LANGUAGE ──────────────────────────────────────────────────
    elif data == "language":
        langs = [
            ("🇬🇧 English", "en"), ("🇮🇳 हिन्दी", "hi"), ("🇷🇺 Русский", "ru"),
            ("🇹🇷 Türkçe", "tr"), ("🇮🇳 தமிழ்", "ta"), ("🇮🇳 മലയാളം", "ml"),
            ("🇮🇳 ಕನ್ನಡ", "kn"), ("🇸🇦 العربية", "ar"), ("🇪🇸 Español", "es"),
        ]
        rows = []
        for label, code in langs:
            rows.append([Button.inline(label, f"set_lang:{code}".encode())])
        rows.append([Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")])
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
                [Button.url(
                    "📤 sʜᴀʀᴇ ʟɪɴᴋ",
                    f"https://t.me/share/url?url={ref_link}"
                    "&text=Buy+Telegram+accounts+instantly!",
                )],
                [Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")],
            ],
        )

    # ── HELP ─────────────────────────────────────────────────────
    elif data == "help":
        await event.edit(
            fancy(
                "❓ **ʜᴇʟᴘ ᴄᴇɴᴛᴇʀ**\n\n"
                "ᴄʜᴏᴏsᴇ ᴀ ᴛᴏᴘɪᴄ ʙᴇʟᴏᴡ:"
            ),
            buttons=[
                [Button.inline("📖 ʜᴏᴡ ᴛᴏ ᴜsᴇ", b"help_howto")],
                [Button.inline("🔑 ᴏᴛᴘ ʜᴇʟᴘ", b"help_otp")],
                [Button.inline("💳 ᴘᴀʏᴍᴇɴᴛ ʜᴇʟᴘ", b"help_payment")],
                [Button.inline("🛡️ ᴡʜʏ ᴛʀᴜsᴛ ᴜs?", b"help_trust")],
                [Button.inline("📚 ꜰᴜʟʟ ᴍᴀɴᴜᴀʟ", b"manual")],
                [Button.inline("📢 ᴜᴘᴅᴀᴛᴇs", b"help_updates")],
                [Button.inline("📞 ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ", b"support")],
                [Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]
            ]
        )

    elif data == "help_howto":
        await event.edit(
            fancy(
                "📖 **ʜᴏᴡ ᴛᴏ ᴜsᴇ**\n\n"
                "1️⃣ ᴀᴅᴅ ᴄʀᴇᴅɪᴛs ᴠɪᴀ ᴜᴘɪ/ᴜsᴅᴛ/sᴛᴀʀs.\n"
                "2️⃣ ɢᴏ ᴛᴏ sᴛᴏʀᴇ → sᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ.\n"
                "3️⃣ ᴄʜᴏᴏsᴇ ᴀ ᴄᴏᴜɴᴛʀʏ → ᴛᴀᴘ ʙᴜʏ.\n"
                "4️⃣ ʀᴇᴄᴇɪᴠᴇ ᴏᴛᴘ & 2ꜰᴀ ɪɴsᴛᴀɴᴛʟʏ.\n"
                "5️⃣ ʀᴇ-ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ ᴡɪᴛʜɪɴ 24ʜ.\n\n"
                "📌 ᴀʟʟ ᴛʀᴀɴsᴀᴄᴛɪᴏɴs ᴀʀᴇ ᴀᴜᴛᴏᴍᴀᴛᴇᴅ."
            ),
            buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"help")]]
        )

    elif data == "help_otp":
        await event.edit(
            fancy(
                "🔑 **ᴏᴛᴘ ᴛʀᴏᴜʙʟᴇsʜᴏᴏᴛɪɴɢ**\n\n"
                "⚠️ ᴛʜᴇ ᴄᴏᴅᴇ ʜᴀs ɴᴏᴛ ᴀʀʀɪᴠᴇᴅ\n"
                "→ ʀᴇǫᴜᴇsᴛ ɪᴛ ᴀɢᴀɪɴ. ᴄᴏᴅᴇs ᴜsᴜᴀʟʟʏ ᴀʀʀɪᴠᴇ ɪɴ <30s.\n\n"
                "❓ ᴡʜᴇʀᴇ ɪs ᴛʜᴇ 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ?\n"
                "→ ᴄʜᴇᴄᴋ ʏᴏᴜʀ ᴏʀᴅᴇʀ ᴅᴇᴛᴀɪʟs.\n\n"
                "❌ ᴛᴇʟᴇɢʀᴀᴍ sᴀʏs ᴛʜᴇ ᴄᴏᴅᴇ ɪs ᴡʀᴏɴɢ\n"
                "→ ᴇɴᴛᴇʀ ᴛʜᴇ ᴄᴏᴅᴇ ᴄᴀʀᴇꜰᴜʟʟʏ ᴏʀ ᴜsᴇ 2ꜰᴀ.\n\n"
                "⏳ ᴍʏ ᴡɪɴᴅᴏᴡ ʀᴀɴ ᴏᴜᴛ\n"
                "→ ʀᴇ-ʀᴇǫᴜᴇsᴛ ғʀᴏᴍ ʜɪsᴛᴏʀʏ."
            ),
            buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"help")]]
        )

    elif data == "help_payment":
        await event.edit(
            fancy(
                "💳 **ᴘᴀʏᴍᴇɴᴛ ʜᴇʟᴘ**\n\n"
                "| ᴍᴇᴛʜᴏᴅ | ᴛɪᴍᴇ |\n"
                "| ᴜᴘɪ / Qʀ | ɪɴsᴛᴀɴᴛ (1–2 ᴍɪɴ) |\n"
                "| ᴜsᴅᴛ (BSC) | 1–3 ʙʟᴏᴄᴋ ᴄᴏɴғɪʀᴍs |\n"
                "| ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs | ɪɴsᴛᴀɴᴛ |\n\n"
                "✔ ᴘᴀɪᴅ ʙᴜᴛ ᴄʀᴇᴅɪᴛs ɴᴏᴛ ᴀᴅᴅᴇᴅ?\n"
                "→ ᴛᴀᴘ 'ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ' ᴏʀ ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ."
            ),
            buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"help")]]
        )

    elif data == "help_trust":
        await event.edit(
            fancy(
                "🛡️ **ᴡʜʏ ᴛʀᴜsᴛ ᴏᴛᴘ ʙᴏᴛ?**\n\n"
                "🔹 100% ᴀᴜᴛᴏᴍᴀᴛᴇᴅ – ɴᴏ ʜᴜᴍᴀɴ ɪɴᴛᴇʀᴠᴇɴᴛɪᴏɴ\n"
                "🔹 ɪɴsᴛᴀɴᴛ ᴏᴛᴘ ғᴏʀᴡᴀʀᴅɪɴɢ\n"
                "🔹 100% ʀᴇꜰᴜɴᴅ ɪғ ɴᴏᴛ ᴅᴇʟɪᴠᴇʀᴇᴅ\n"
                "🔹 ɴᴏ ʜᴜᴍᴀɴ ᴇᴠᴇʀ sᴇᴇs sᴇssɪᴏɴ ᴅᴀᴛᴀ\n"
                "🔹 ᴀᴜᴛᴏᴍᴀᴛᴇᴅ ᴇᴍᴀɪʟ & 2ꜰᴀ ʀᴏᴛᴀᴛɪᴏɴ\n\n"
                "📌 ᴏɴʟʏ ᴡʜᴀᴛsᴀᴘᴘ ᴏʀᴅᴇʀs ᴀʀᴇ ᴍᴀɴᴜᴀʟ."
            ),
            buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"help")]]
        )

    elif data == "help_updates":
        await event.edit(
            fancy("📢 **ᴜᴘᴅᴀᴛᴇs**\n\nᴍᴏʀᴇ ʙᴏᴛs ᴀɴᴅ ғᴇᴀᴛᴜʀᴇs ᴄᴏᴍɪɴɢ sᴏᴏɴ!"),
            buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"help")]]
        )

    elif data == "manual":
        await event.edit(
            fancy(
                "📚 **ꜰᴜʟʟ ᴍᴀɴᴜᴀʟ**\n\n"
                "1. **ʙᴜʏɪɴɢ ᴀᴄᴄᴏᴜɴᴛs** (100% ᴀᴜᴛᴏᴍᴀᴛᴇᴅ)\n"
                "   ᴀᴅᴅ ᴄʀᴇᴅɪᴛs → sᴛᴏʀᴇ → ᴄᴀᴛᴇɢᴏʀʏ → ᴄᴏᴜɴᴛʀʏ → ʙᴜʏ\n"
                "   → ɢᴇᴛ ᴏᴛᴘ & 2ꜰᴀ\n\n"
                "2. **ᴡʜᴀᴛsᴀᴘᴘ ɴᴜᴍʙᴇʀs** (ᴏᴘᴇʀᴀᴛᴏʀ ᴅᴇʟɪᴠᴇʀʏ)\n\n"
                "3. **ᴘᴀʏᴍᴇɴᴛs & ᴛᴏᴘ-ᴜᴘs**\n"
                "   ᴜᴘɪ/ǫʀ, ᴜsᴅᴛ, ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs\n\n"
                "4. **ʀᴇꜰᴇʀ & ᴇᴀʀɴ**\n"
                "   sʜᴀʀᴇ ʟɪɴᴋ → ɢᴇᴛ ʙᴏɴᴜs ᴏɴ ᴊᴏɪɴ & ᴅᴇᴘᴏsɪᴛ\n\n"
                "5. **sᴇᴄᴜʀɪᴛʏ & ʀᴇꜰᴜɴᴅ ɢᴜᴀʀᴀɴᴛᴇᴇs**\n"
                "   100% ᴀᴜᴛᴏᴍᴀᴛᴇᴅ, ʀᴇꜰᴜɴᴅ ɪғ ᴜɴᴅᴇʟɪᴠᴇʀᴇᴅ"
            ),
            buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"help")]]
        )

    elif data == "support":
        support_link = await get_setting("support_link")
        if support_link:
            await event.edit(
                fancy("📞 **ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ**\n\nᴛᴀᴘ ʙᴇʟᴏᴡ ᴛᴏ ᴄʜᴀᴛ."),
                buttons=[[Button.url("📞 sᴜᴘᴘᴏʀᴛ", support_link)],
                         [Button.inline("◀️ ʙᴀᴄᴋ", b"help")]]
            )
        else:
            await event.edit(
                fancy("📞 **sᴜᴘᴘᴏʀᴛ**\n\nɴᴏ sᴜᴘᴘᴏʀᴛ ʟɪɴᴋ ᴄᴏɴғɪɢᴜʀᴇᴅ."),
                buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"help")]]
            )

    # ── WHATSAPP ──────────────────────────────────────────────────
    elif data == "whatsapp":
        await event.edit(
            fancy("💬 **ᴡʜᴀᴛsᴀᴘᴘ**\n\nᴄᴏɴᴛᴀᴄᴛ ᴏᴜʀ ᴡʜᴀᴛsᴀᴘᴘ sᴜᴘᴘᴏʀᴛ ғᴏʀ ᴀssɪsᴛᴀɴᴄᴇ."),
            buttons=[[Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]]
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
            buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"admin")]],
        )

    # ── UPLOAD SESSIONS ─────────────────────────────────────────
    elif data == "upload_sessions":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        cats = await get_categories()
        rows = []
        for cat in cats:
            rows.append([Button.inline(
                f"{cat['icon']} {cat['name']}",
                f"upload_cat:{cat['name']}".encode()
            )])
        rows.append([Button.inline("◀️ ʙᴀᴄᴋ", b"admin")])
        await event.edit(fancy("📦 **ᴜᴘʟᴏᴀᴅ sᴇssɪᴏɴs**\n\nsᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ:"), buttons=rows)

    elif data.startswith("upload_cat:"):
        cat_name = data.split(":", 1)[1]
        c_list = await countries_col.find({"is_active": True}).to_list(50)
        rows = []
        for c in c_list:
            rows.append([Button.inline(
                f"{c['flag']} {c['name']}",
                f"upload_country:{c['code']}:{cat_name}".encode()
            )])
        rows.append([Button.inline("◀️ ʙᴀᴄᴋ", b"admin")])
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
        # Show default 2FA hint
        default_2fa = await get_setting("default_2fa", "")
        hint = f"\n\n💡 ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ: `{default_2fa}`" if default_2fa else ""
        await event.edit(
            fancy(
                f"📦 **ᴜᴘʟᴏᴀᴅ sᴇssɪᴏɴs — {c['flag']} {c['name']} ({cat_name})**\n\n"
                "sᴛᴇᴘ 1 (ᴏᴘᴛɪᴏɴᴀʟ): sᴇɴᴅ 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ ᴀs ᴛᴇxᴛ.\n"
                f"ɪғ ɴᴏᴛ sᴇɴᴛ, ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ ᴡɪʟʟ ʙᴇ ᴜsᴇᴅ.{hint}\n"
                "sᴛᴇᴘ 2: sᴇɴᴅ ᴛʜᴇ **.ᴢɪᴘ** ᴏʀ **.sᴇssɪᴏɴ** ꜰɪʟᴇ."
            ),
            buttons=[[Button.inline("❌ ᴄᴀɴᴄᴇʟ", b"admin")]],
        )

    # ── MANAGE SESSIONS ──────────────────────────────────────────
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
            buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"admin")]],
        )

    # ── PENDING DEPOSITS ──────────────────────────────────────────
    elif data == "pending_deposits":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        deps = await deposits_col.find(
            {"status": "pending"}).sort("created_at", 1).limit(10).to_list(10)
        if not deps:
            await event.edit(
                fancy("✅ ɴᴏ ᴘᴇɴᴅɪɴɢ ᴅᴇᴘᴏsɪᴛs."),
                buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"admin")]],
            )
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
                fancy(
                    f"💳 **ᴅᴇᴘᴏsɪᴛ ʀᴇǫᴜᴇsᴛ**\n"
                    f"ᴜsᴇʀ ɪᴅ: `{uid}`\n"
                    f"ᴀᴍᴏᴜɴᴛ: `₹{amount:.2f}`\n"
                    f"ᴛɪᴍᴇ: {created}"
                ),
                buttons=[
                    [Button.inline(
                        "✅ ᴀᴘᴘʀᴏᴠᴇ",
                        f"dep_approve:{dep_id}:{uid}:{amount}".encode(),
                    ),
                    Button.inline(
                        "❌ ʀᴇᴊᴇᴄᴛ",
                        f"dep_reject:{dep_id}:{uid}".encode(),
                    )],
                ],
            )

    # ── DEPOSIT APPROVE/REJECT ──────────────────────────────────
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
            {"$set": {
                "status":      "approved",
                "approved_at": datetime.utcnow(),
                "approved_by": user_id,
            }},
        )
        if claim.matched_count == 0:
            await event.answer("⚠️ Already processed.", alert=True)
            await event.edit("⏭️ ᴛʜɪs ᴅᴇᴘᴏsɪᴛ ᴡᴀs ᴀʟʀᴇᴀᴅʏ ᴘʀᴏᴄᴇssᴇᴅ.")
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
                    {"$inc": {"balance": bonus, "referral_earnings": bonus,
                              "withdrawable": bonus}},
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
            await bot.send_message(
                uid,
                fancy(f"✅ **ᴅᴇᴘᴏsɪᴛ ᴀᴘᴘʀᴏᴠᴇᴅ!**\n`₹{amount:.2f}` ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ."),
            )
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
            {"$set": {
                "status":      "rejected",
                "rejected_at": datetime.utcnow(),
                "rejected_by": user_id,
            }},
            return_document=True,
        )
        if dep_doc is None:
            await event.answer("⚠️ Already processed.", alert=True)
            await event.edit("⏭️ ᴛʜɪs ᴅᴇᴘᴏsɪᴛ ᴡᴀs ᴀʟʀᴇᴀᴅʏ ᴘʀᴏᴄᴇssᴇᴅ.")
            return
        uid = int(dep_doc.get("user_id", uid))
        try:
            await bot.send_message(
                uid,
                fancy("❌ **ᴅᴇᴘᴏsɪᴛ ʀᴇᴊᴇᴄᴛᴇᴅ.**\nᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ."),
            )
        except Exception:
            pass
        await event.edit(f"❌ ʀᴇᴊᴇᴄᴛᴇᴅ ᴅᴇᴘᴏsɪᴛ ғᴏʀ ᴜsᴇʀ `{uid}`.")

    # ── BROADCAST ────────────────────────────────────────────────
    elif data == "broadcast":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "broadcast"}
        await event.edit(
            fancy("📢 **ʙʀᴏᴀᴅᴄᴀsᴛ**\n\nsᴇɴᴅ ᴛʜᴇ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ᴛᴏ ᴀʟʟ ᴜsᴇʀs."),
            buttons=[[Button.inline("❌ ᴄᴀɴᴄᴇʟ", b"admin")]],
        )

    # ── SETTINGS ──────────────────────────────────────────────────
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
                f"🤖 ʙᴏᴛ ɴᴀᴍᴇ: `{bn}`\n"
                f"💳 ᴜᴘɪ ɪᴅ: `{upi}`\n"
                f"👤 ᴜᴘɪ ɴᴀᴍᴇ: `{uname_}`\n"
                f"📞 sᴜᴘᴘᴏʀᴛ ʟɪɴᴋ: `{sup}`\n"
                f"🎁 ʀᴇꜰ ʙᴏɴᴜs: `₹{rb}`\n"
                f"📈 ʀᴇꜰ %: `{rp}%`\n"
                f"🔢 ᴍɪɴ ᴅᴇᴘᴏsɪᴛ: `₹{md}`\n"
                f"💬 ᴡʜᴀᴛsᴀᴘᴘ: {wa}\n"
                f"🖼️ ᴡᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ: {photo}\n"
                f"⏳ ᴀᴜᴛᴏ ᴠᴇʀɪғʏ ᴅᴇʟᴀʏ: `{delay}s`\n"
                f"🌐 ᴘᴀʏᴍᴇɴᴛ ᴡᴇʙʜᴏᴏᴋ: `{webhook}`\n"
                f"🔐 ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ: `{default2fa or 'ɴᴏɴᴇ'}`"
            ),
            buttons=[
                [Button.inline("🤖 ʙᴏᴛ ɴᴀᴍᴇ", b"set_botname"),
                 Button.inline("💳 ᴜᴘɪ ɪᴅ", b"set_upi")],
                [Button.inline("👤 ᴜᴘɪ ɴᴀᴍᴇ", b"set_upiname"),
                 Button.inline("📞 sᴜᴘᴘᴏʀᴛ", b"set_support")],
                [Button.inline("🎁 ʀᴇꜰ ʙᴏɴᴜs", b"set_ref_bonus"),
                 Button.inline("📈 ʀᴇꜰ %", b"set_ref_pct")],
                [Button.inline("🔢 ᴍɪɴ ᴅᴇᴘᴏsɪᴛ", b"set_min_dep")],
                [Button.inline("💬 ᴛᴏɢɢʟᴇ ᴡʜᴀᴛsᴀᴘᴘ", b"toggle_whatsapp"),
                 Button.inline("🖼️ sᴇᴛ ᴡᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ", b"set_welcome_photo")],
                [Button.inline("⏳ ᴀᴜᴛᴏ ᴠᴇʀɪғʏ ᴅᴇʟᴀʏ", b"set_verify_delay"),
                 Button.inline("🌐 ᴘᴀʏᴍᴇɴᴛ ᴡᴇʙʜᴏᴏᴋ", b"set_webhook")],
                [Button.inline("🔐 ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ", b"set_default_2fa")],
                [Button.inline("◀️ ʙᴀᴄᴋ", b"admin")],
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
                             buttons=[[Button.inline("◀️ ʙᴀᴄᴋ", b"asettings")]])
            return
        elif data == "set_welcome_photo":
            user_states[user_id] = {"state": "welcome_photo_set"}
            await event.edit(prompt, buttons=[[Button.inline("❌ ᴄᴀɴᴄᴇʟ", b"asettings")]])
            return
        user_states[user_id] = {"state": sk}
        await event.edit(fancy(prompt), buttons=[[Button.inline("❌ ᴄᴀɴᴄᴇʟ", b"asettings")]])

    # ── COUNTRIES ──────────────────────────────────────────────────
    elif data == "acountries":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        c_list = await countries_col.find({}).to_list(50)
        rows   = []
        for c in c_list:
            em = "✅" if c.get("is_active") else "❌"
            rows.append([Button.inline(
                f"{em} {c['flag']} {c['name']} — ₹{c['price']:.0f}",
                f"ctoggle:{c['code']}".encode(),
            )])
        rows.append([Button.inline("➕ ᴀᴅᴅ ᴄᴏᴜɴᴛʀʏ", b"add_country"),
                     Button.inline("◀️ ʙᴀᴄᴋ", b"admin")])
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
            buttons=[[Button.inline("❌ ᴄᴀɴᴄᴇʟ", b"acountries")]],
        )

    # ── USERS ────────────────────────────────────────────────────
    elif data == "ausers":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "search_user"}
        await event.edit(
            fancy("👤 **ᴜsᴇʀ ʟᴏᴏᴋᴜᴘ**\n\nsᴇɴᴅ ᴛʜᴇ ᴛᴇʟᴇɢʀᴀᴍ ᴜsᴇʀ ɪᴅ:"),
            buttons=[
                [Button.inline("➕ ᴀᴅᴅ ʙᴀʟᴀɴᴄᴇ", b"admin_add_bal"),
                 Button.inline("🚫 ʙᴀɴ/ᴜɴʙᴀɴ", b"admin_ban")],
                [Button.inline("◀️ ʙᴀᴄᴋ", b"admin")],
            ],
        )

    elif data == "admin_add_bal":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "add_bal_uid"}
        await event.edit(
            fancy("💰 **ᴀᴅᴅ ʙᴀʟᴀɴᴄᴇ**\n\nsᴇɴᴅ ᴜsᴇʀ ɪᴅ:"),
            buttons=[[Button.inline("❌ ᴄᴀɴᴄᴇʟ", b"admin")]],
        )

    elif data == "admin_ban":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "ban_uid"}
        await event.edit(
            fancy("🚫 **ʙᴀɴ/ᴜɴʙᴀɴ**\n\nsᴇɴᴅ ᴜsᴇʀ ɪᴅ:"),
            buttons=[[Button.inline("❌ ᴄᴀɴᴄᴇʟ", b"admin")]],
        )

    # ── MANAGE ADMINS ────────────────────────────────────────────
    elif data == "manage_admins":
        if user_id != OWNER_ID:
            await event.answer("❌ Owner only!", alert=True)
            return
        admins = await bot_admins_col.find({"is_active": True}).to_list(50)
        rows   = []
        for a in admins:
            rows.append([Button.inline(
                f"🔴 ʀᴇᴍᴏᴠᴇ {a.get('name', a['telegram_id'])}",
                f"rm_admin:{a['telegram_id']}".encode(),
            )])
        rows.append([Button.inline("➕ ᴀᴅᴅ ᴀᴅᴍɪɴ", b"add_admin"),
                     Button.inline("◀️ ʙᴀᴄᴋ", b"admin")])
        await event.edit(
            fancy(f"🔑 **ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs**\n\nᴏᴡɴᴇʀ: `{OWNER_ID}`\nᴇxᴛʀᴀ ᴀᴅᴍɪɴs: {len(admins)}"),
            buttons=rows,
        )

    elif data == "add_admin":
        if user_id != OWNER_ID:
            await event.answer("❌ Owner only!", alert=True)
            return
        user_states[user_id] = {"state": "add_admin"}
        await event.edit(
            fancy("🔑 **ᴀᴅᴅ ᴀᴅᴍɪɴ**\n\nsᴇɴᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴜsᴇʀ ɪᴅ:"),
            buttons=[[Button.inline("❌ ᴄᴀɴᴄᴇʟ", b"manage_admins")]],
        )

    elif data.startswith("rm_admin:"):
        if user_id != OWNER_ID:
            await event.answer("❌ Owner only!", alert=True)
            return
        rm_id = int(data.split(":")[1])
        await bot_admins_col.update_one(
            {"telegram_id": rm_id}, {"$set": {"is_active": False}}
        )
        await event.answer(f"Removed admin {rm_id}")
        try:
            await bot.send_message(rm_id, "🔑 Your admin access has been removed.")
        except Exception:
            pass
        admins = await bot_admins_col.find({"is_active": True}).to_list(50)
        rows   = [[Button.inline(
            f"🔴 ʀᴇᴍᴏᴠᴇ {a.get('name', a['telegram_id'])}",
            f"rm_admin:{a['telegram_id']}".encode(),
        )] for a in admins]
        rows.append([Button.inline("➕ ᴀᴅᴅ ᴀᴅᴍɪɴ", b"add_admin"),
                     Button.inline("◀️ ʙᴀᴄᴋ", b"admin")])
        await event.edit(fancy("🔑 **ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs**:"), buttons=rows)

    # ── LEGACY BALANCE ────────────────────────────────────────────
    elif data == "balance":
        if not user:
            user = await get_or_create_user(user_id)
        bal   = float(user.get("balance", 0))
        spent = 0.0
        async for o in orders_col.find(
                {"user_id": user_id, "status": {"$nin": ["cancelled"]}}):
            spent += float(o.get("amount", 0))
        await event.edit(
            fancy(
                f"💰 **ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ**\n\n"
                f"ᴀᴠᴀɪʟᴀʙʟᴇ: `₹{bal:.2f}`\n"
                f"ᴛᴏᴛᴀʟ sᴘᴇɴᴛ: `₹{spent:.2f}`"
            ),
            buttons=[
                [Button.inline("💳 ᴀᴅᴅ ᴄʀᴇᴅɪᴛs", b"deposit")],
                [Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")],
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
    # Call webhook if configured
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
    # Mock approval (for demo)
    await _approve_deposit(deposit_id)

async def _approve_deposit(deposit_id: str):
    dep = await deposits_col.find_one({"deposit_id": deposit_id, "status": "pending"})
    if not dep:
        return
    await deposits_col.update_one(
        {"deposit_id": deposit_id},
        {"$set": {"status": "approved", "approved_at": datetime.utcnow()}}
    )
    uid = dep["user_id"]
    amount = dep["amount"]
    credits = dep.get("credits", 0)
    await users_col.update_one({"user_id": uid}, {"$inc": {"balance": amount, "withdrawable": amount}})
    try:
        await bot.send_message(
            uid,
            fancy(f"✅ **ᴘᴀʏᴍᴇɴᴛ ᴄᴏɴғɪʀᴍᴇᴅ!**\n`{credits} ᴄʀᴇᴅɪᴛs` ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ.")
        )
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

    # ── CUSTOM DEPOSIT ──────────────────────────────────────────
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
        upi_id = await get_setting("upi_id")
        upi_name = await get_setting("upi_name", "Next Level Vault")
        qr = _make_upi_qr(upi_id, amount, upi_name)
        upi_deep_link = f"upi://pay?pa={upi_id}&pn={upi_name}&am={amount:.2f}&cu=INR"
        msg = fancy(
            f"💳 **ᴜᴘɪ ᴘᴀʏᴍᴇɴᴛ**\n\n"
            f"ᴀᴍᴏᴜɴᴛ: `₹{amount:.2f}`\n"
            f"ᴄʀᴇᴅɪᴛs: `+{credits}`\n"
            f"ᴜᴘɪ ɪᴅ: `{upi_id}`\n\n"
            "sᴄᴀɴ Qʀ ᴏʀ ᴜsᴇ 'ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ'."
        )
        if qr:
            qr_file = io.BytesIO(qr)
            qr_file.name = "upi_qr.png"
            await event.respond(file=qr_file)
        await event.respond(msg, buttons=[
            [Button.url("📲 ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ", upi_deep_link)],
            [Button.inline("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ", b"deposit_paid")],
            [Button.inline("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ", b"check_payment")],
            [Button.inline("◀️ ʙᴀᴄᴋ", b"deposit")]
        ])
        user_states.pop(user_id, None)  # deposit_id set separately

    # ── 2FA PASSWORD ─────────────────────────────────────────────
    elif state == "waiting_zip" and not event.message.file:
        twofa = text.strip()
        user_states[user_id] = {**state_data, "twofa_password": twofa}
        await event.respond(
            fancy(f"🔐 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ sᴀᴠᴇᴅ: `{twofa}`\n\nɴᴏᴡ sᴇɴᴅ ᴛʜᴇ **.ᴢɪᴘ** ᴏʀ **.sᴇssɪᴏɴ** ꜰɪʟᴇ."),
            buttons=[[Button.inline("❌ ᴄᴀɴᴄᴇʟ", b"admin")]],
        )

    # ── ZIP or SINGLE SESSION FILE ─────────────────────────────
    elif state == "waiting_zip" and (event.message.file or event.message.media):
        prog = await event.respond(fancy("⏳ ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ ꜰɪʟᴇ…"))
        try:
            file_bytes = await event.message.download_media(bytes)
        except Exception as e:
            await prog.edit(f"❌ ᴅᴏᴡɴʟᴏᴀᴅ ꜰᴀɪʟᴇᴅ: {e}")
            return

        # Detect if it's a ZIP or single .session
        if event.message.file and event.message.file.name and event.message.file.name.lower().endswith('.session'):
            # Single session file
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
            await prog.edit(
                fancy(f"✅ **ꜰɪʟᴇ ᴜᴘʟᴏᴀᴅᴇᴅ**\n`{phone}` ᴀᴅᴅᴇᴅ ᴛᴏ {state_data['country_name']}.")
            )
            user_states.pop(user_id, None)
            return

        # ZIP file
        try:
            zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        except Exception as e:
            await prog.edit(f"❌ Invalid ZIP: {e}")
            user_states.pop(user_id, None)
            return

        # Check for 2fa.txt inside ZIP
        twofa = state_data.get("twofa_password", "")
        try:
            info = zf.getinfo("2fa.txt")
            twofa = zf.read(info).decode().strip()
            log.info(f"[zip] Found 2fa.txt: {twofa}")
        except KeyError:
            pass
        # If still empty, use default_2fa
        if not twofa:
            twofa = await get_setting("default_2fa", "")

        all_names = [n for n in zf.namelist() if n.lower().endswith(".session")]
        if not all_names:
            await prog.edit("❌ No .session files found in ZIP.")
            user_states.pop(user_id, None)
            return

        total = len(all_names)
        sem = asyncio.Semaphore(5)  # parallel connect
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

        result = fancy(
            f"📦 **ᴜᴘʟᴏᴀᴅ ᴄᴏᴍᴘʟᴇᴛᴇ**\n\n"
            f"✅ ᴀᴅᴅᴇᴅ: `{added}`\n"
            f"⏭️ sᴋɪᴘᴘᴇᴅ: `{skipped}`\n"
        )
        if errors:
            shown = errors[:5]
            result += f"❌ ꜰᴀɪʟᴇᴅ ({len(errors)}): `{', '.join(shown)}`"
        await prog.edit(result, buttons=[[Button.inline("◀️ ᴀᴅᴍɪɴ", b"admin")]])
        user_states.pop(user_id, None)
        zf.close()

    # ── BROADCAST ──────────────────────────────────────────────────
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

    # ── SETTINGS VALUES ────────────────────────────────────────────
    elif state == "setting_botname":
        await set_setting("bot_name", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ʙᴏᴛ ɴᴀᴍᴇ ᴜᴘᴅᴀᴛᴇᴅ."),
                            buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])

    elif state == "setting_upi":
        await set_setting("upi_id", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ᴜᴘɪ ɪᴅ ᴜᴘᴅᴀᴛᴇᴅ."),
                            buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])

    elif state == "setting_upiname":
        await set_setting("upi_name", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ᴜᴘɪ ɴᴀᴍᴇ ᴜᴘᴅᴀᴛᴇᴅ."),
                            buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])

    elif state == "setting_support":
        await set_setting("support_link", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ sᴜᴘᴘᴏʀᴛ ʟɪɴᴋ ᴜᴘᴅᴀᴛᴇᴅ."),
                            buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])

    elif state == "setting_ref_bonus":
        try:
            val = float(text.strip())
            await set_setting("referral_bonus", val)
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ ʀᴇꜰᴇʀʀᴀʟ ʙᴏɴᴜs sᴇᴛ ᴛᴏ ₹{val:.0f}."),
                                buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))

    elif state == "setting_ref_pct":
        try:
            val = float(text.strip())
            await set_setting("referral_percent", val)
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ ʀᴇꜰᴇʀʀᴀʟ % sᴇᴛ ᴛᴏ {val:.1f}%."),
                                buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))

    elif state == "setting_min_dep":
        try:
            val = float(text.strip())
            await set_setting("min_deposit", val)
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ ᴍɪɴɪᴍᴜᴍ ᴅᴇᴘᴏsɪᴛ sᴇᴛ ᴛᴏ ₹{val:.0f}."),
                                buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))

    elif state == "setting_verify_delay":
        try:
            val = int(text.strip())
            await set_setting("auto_verify_delay", val)
            user_states.pop(user_id, None)
            await event.respond(fancy(f"✅ ᴀᴜᴛᴏ ᴠᴇʀɪғʏ ᴅᴇʟᴀʏ sᴇᴛ ᴛᴏ {val}s."),
                                buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))

    elif state == "setting_webhook":
        await set_setting("payment_webhook_url", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ᴘᴀʏᴍᴇɴᴛ ᴡᴇʙʜᴏᴏᴋ ᴜʀʟ sᴇᴛ."),
                            buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])

    elif state == "setting_default_2fa":
        await set_setting("default_2fa", text.strip())
        user_states.pop(user_id, None)
        await event.respond(fancy("✅ ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ sᴇᴛ."),
                            buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])

    # ── WELCOME PHOTO ────────────────────────────────────────────
    elif state == "welcome_photo_set":
        if event.message.photo:
            if hasattr(event.message.media, 'photo') and hasattr(event.message.media.photo, 'id'):
                file_id = str(event.message.media.photo.id)
                await set_setting("welcome_photo", file_id)
                user_states.pop(user_id, None)
                await event.respond(fancy("🖼️ ᴡᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ sᴇᴛ!"),
                                    buttons=[[Button.inline("◀️ sᴇᴛᴛɪɴɢs", b"asettings")]])
            else:
                await event.respond(fancy("❌ ᴜɴᴀʙʟᴇ ᴛᴏ ɢᴇᴛ ᴘʜᴏᴛᴏ ɪᴅ."))
        else:
            await event.respond(fancy("❌ ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴘʜᴏᴛᴏ."))

    # ── ADD COUNTRY ──────────────────────────────────────────────
    elif state == "add_country":
        try:
            parts = [p.strip() for p in text.strip().split("|")]
            code, name, flag_, price_str = (
                parts[0].upper(), parts[1], parts[2], parts[3]
            )
            price_val = float(price_str)
            existing  = await countries_col.find_one({"code": code})
            if existing:
                await countries_col.update_one(
                    {"code": code},
                    {"$set": {"name": name, "flag": flag_,
                              "price": price_val, "is_active": True}},
                )
                msg = f"♻️ **{name}** ᴜᴘᴅᴀᴛᴇᴅ (₹{price_val:.0f})."
            else:
                await countries_col.insert_one({
                    "code":      code,
                    "name":      name,
                    "flag":      flag_,
                    "price":     price_val,
                    "is_active": True,
                })
                msg = f"✅ **{name}** ᴀᴅᴅᴇᴅ (₹{price_val:.0f})."
            user_states.pop(user_id, None)
            await event.respond(fancy(msg),
                                buttons=[[Button.inline("◀️ ᴄᴏᴜɴᴛʀɪᴇs", b"acountries")]])
        except Exception:
            await event.respond(
                fancy("❌ ᴡʀᴏɴɢ ꜰᴏʀᴍᴀᴛ. ᴜsᴇ:\n`CODE | Name | Flag | Price`")
            )

    # ── USER SEARCH ──────────────────────────────────────────────
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
            await event.respond(
                fancy(
                    f"👤 **ᴜsᴇʀ ɪɴꜰᴏ**\n\n"
                    f"ɪᴅ: `{tid}`\n"
                    f"ʙᴀʟᴀɴᴄᴇ: `₹{target.get('balance', 0):.2f}`\n"
                    f"ᴏʀᴅᴇʀs: `{o_count}`\n"
                    f"ᴅᴇᴘᴏsɪᴛs: `{d_count}`\n"
                    f"ʙᴀɴɴᴇᴅ: {banned_}\n"
                    f"ᴊᴏɪɴᴇᴅ: {target.get('joined_at','?')}"
                )
            )
            user_states.pop(user_id, None)
        except ValueError:
            await event.respond(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ."))

    # ── ADD BALANCE UID ──────────────────────────────────────────
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
                await bot.send_message(
                    tid,
                    fancy(f"💰 **₹{amount:.0f} ᴀᴅᴅᴇᴅ** ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ ʙʏ ᴀᴅᴍɪɴ!"),
                )
            except Exception:
                pass
            user_states.pop(user_id, None)
            await event.respond(
                fancy(f"✅ ₹{amount:.0f} ᴀᴅᴅᴇᴅ ᴛᴏ ᴜsᴇʀ `{tid}`."),
                buttons=[[Button.inline("◀️ ᴀᴅᴍɪɴ", b"admin")]],
            )
        except ValueError:
            await event.respond(fancy("❌ ɪɴᴠᴀʟɪᴅ ᴀᴍᴏᴜɴᴛ."))

    # ── BAN / UNBAN ──────────────────────────────────────────────
    elif state == "ban_uid":
        try:
            tid    = int(text.strip())
            target = await users_col.find_one({"user_id": tid})
            if not target:
                await event.respond(fancy("❌ ᴜsᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ."))
                user_states.pop(user_id, None)
                return
            new_ban = not target.get("is_banned", False)
            await users_col.update_one(
                {"user_id": tid}, {"$set": {"is_banned": new_ban}}
            )
            action = "ʙᴀɴɴᴇᴅ" if new_ban else "ᴜɴʙᴀɴɴᴇᴅ"
            try:
                await bot.send_message(
                    tid,
                    fancy("🚫 ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ **ʙᴀɴɴᴇᴅ** ғʀᴏᴍ ᴛʜɪs ʙᴏᴛ."
                          if new_ban else
                          "✅ ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ **ᴜɴʙᴀɴɴᴇᴅ**. ᴡᴇʟᴄᴏᴍᴇ ʙᴀᴄᴋ!"),
                )
            except Exception:
                pass
            user_states.pop(user_id, None)
            await event.respond(
                fancy(f"✅ ᴜsᴇʀ `{tid}` ʜᴀs ʙᴇᴇɴ **{action}**."),
                buttons=[[Button.inline("◀️ ᴀᴅᴍɪɴ", b"admin")]],
            )
        except ValueError:
            await event.respond(fancy("❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ."))

    # ── ADD ADMIN ──────────────────────────────────────────────────
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
                await bot_admins_col.update_one(
                    {"telegram_id": new_id}, {"$set": {"is_active": True}}
                )
            else:
                await bot_admins_col.insert_one({
                    "telegram_id": new_id,
                    "name":        name_,
                    "username":    uname_,
                    "is_active":   True,
                    "added_by":    user_id,
                    "added_at":    datetime.utcnow(),
                })
            user_states.pop(user_id, None)
            await event.respond(
                fancy(f"✅ **ᴀᴅᴍɪɴ ᴀᴅᴅᴇᴅ:** {name_} (`{new_id}`)"),
                buttons=[[Button.inline("◀️ ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs", b"manage_admins")]],
            )
            try:
                await bot.send_message(
                    new_id, fancy("🔑 ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ ɢʀᴀɴᴛᴇᴅ **ᴀᴅᴍɪɴ ᴀᴄᴄᴇss** ᴛᴏ ᴛʜᴇ ʙᴏᴛ!")
                )
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
                        buttons=[[Button.inline("🏠 ʜᴏᴍᴇ", b"main_menu")]])

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
            await event.respond(
                fancy(
                    f"👤 **ᴜsᴇʀ ɪɴꜰᴏ**\n\n"
                    f"ɪᴅ: `{uid}`\n"
                    f"ʙᴀʟᴀɴᴄᴇ: `₹{user.get('balance',0):.2f}`\n"
                    f"ᴏʀᴅᴇʀs: `{orders}`\n"
                    f"ᴅᴇᴘᴏsɪᴛs: `{deps}`"
                )
            )
            return
    except ValueError:
        pass
    order = await orders_col.find_one({"_id": target})
    if order:
        await event.respond(
            fancy(
                f"📋 **ᴏʀᴅᴇʀ ɪɴꜰᴏ**\n\n"
                f"ɪᴅ: `{target}`\n"
                f"ᴜsᴇʀ: `{order.get('user_id')}`\n"
                f"ᴄᴏᴜɴᴛʀʏ: {order.get('country_flag','')} {order.get('country')}\n"
                f"ᴘʜᴏɴᴇ: `{order.get('phone')}`\n"
                f"sᴛᴀᴛᴜs: `{order.get('status')}`"
            )
        )
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
            await bot.send_message(
                admin_id,
                fancy(f"📢 **ʀᴇǫᴜᴇsᴛ ғᴏʀ ʀᴇsᴛᴏᴄᴋ**\n\nᴜsᴇʀ `{event.sender_id}` ʀᴇǫᴜᴇsᴛs `{cc}`.")
            )
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
            await bot.send_message(
                admin_id,
                fancy(f"💬 **ɴᴇᴡ ꜰᴇᴇᴅʙᴀᴄᴋ**\n\nᴜsᴇʀ `{event.sender_id}`:\n{feedback}")
            )
        except Exception:
            pass
    await event.respond(fancy("✅ ᴛʜᴀɴᴋ ʏᴏᴜ ғᴏʀ ʏᴏᴜʀ ꜰᴇᴇᴅʙᴀᴄᴋ!"))

# ─── 23. SELF‑PING (Keep bot awake on Render) ──────────────────
async def self_ping():
    """Ping the /ping endpoint every 4 minutes to avoid Render sleep."""
    while True:
        await asyncio.sleep(240)  # 4 minutes
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
    # This is a placeholder – real implementation would verify signature
    # and call _approve_deposit(deposit_id)
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
    acc_mgr = AccountManager(
        accounts_col, bot, API_ID, API_HASH, pending_otp_requests
    )
    await acc_mgr.load_all()

    # Start self-ping if RENDER_EXTERNAL_URL is set
    if RENDER_EXTERNAL_URL:
        asyncio.create_task(self_ping())

    bot_name = await get_setting("bot_name", "Next Level Vault")
    log.info(f"🚀 {bot_name} is running…")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
