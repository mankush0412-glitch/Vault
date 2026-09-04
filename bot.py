"""
╔══════════════════════════════════════════════════╗
║              STARK BOT                           ║
║   Telegram Account (OTP) Seller Bot              ║
║   Single-file.  Run:  python bot.py              ║
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
from datetime import datetime
from typing import Optional, List, Dict

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from telethon import TelegramClient, events, Button, functions
from telethon.sessions import StringSession
from telethon.errors import (
    UserNotParticipantError,
    ChatAdminRequiredError,
    ChannelPrivateError,
)
from account_manager import AccountManager

# ══════════════════════════════════════════════════════════════
#  1.  ENV / CONFIG
# ══════════════════════════════════════════════════════════════
load_dotenv()

API_ID    = int(os.getenv("API_ID", "0"))
API_HASH  = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
OWNER_ID  = int(os.getenv("OWNER_ID", "0"))
DB_NAME   = os.getenv("DB_NAME", "stark_bot")

# Extra admins from .env  (comma-separated IDs)
ADMIN_IDS: List[int] = [OWNER_ID] if OWNER_ID else []
for _a in os.getenv("ADMIN_IDS", "").split(","):
    try:
        _id = int(_a.strip())
        if _id not in ADMIN_IDS:
            ADMIN_IDS.append(_id)
    except ValueError:
        pass

# Force-join channels  (comma-separated @username or numeric chat ID)
_fj_raw = os.getenv("FORCE_JOIN_CHAT_IDS", os.getenv("FORCE_JOIN_CHAT_ID", "")).strip()
RAW_CHAT_IDS: List[str] = [x.strip() for x in _fj_raw.split(",") if x.strip()]

if not all([API_ID, API_HASH, BOT_TOKEN, OWNER_ID]):
    raise ValueError("❌ .env incomplete!  Set API_ID, API_HASH, BOT_TOKEN, OWNER_ID.")

# ══════════════════════════════════════════════════════════════
#  2.  LOGGING
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("StarkBot")

# ══════════════════════════════════════════════════════════════
#  3.  MONGODB
# ══════════════════════════════════════════════════════════════
_mongo_client    = AsyncIOMotorClient(MONGO_URL)
db               = _mongo_client[DB_NAME]
accounts_col     = db["accounts"]     # session strings + metadata
users_col        = db["users"]        # buyers / members
orders_col       = db["orders"]       # purchase records
deposits_col     = db["deposits"]     # deposit requests
settings_col     = db["settings"]     # key-value config
countries_col    = db["countries"]    # country list with prices
bot_admins_col   = db["bot_admins"]   # extra admins (beyond owner)

# ══════════════════════════════════════════════════════════════
#  4.  BOT INSTANCE
# ══════════════════════════════════════════════════════════════
_session_name = "stark_bot_" + hashlib.md5(BOT_TOKEN.encode()).hexdigest()[:8]
bot = TelegramClient(_session_name, API_ID, API_HASH)

# ── Global state ──────────────────────────────────────────────
pending_otp_requests: Dict = {}   # (buyer_id, phone) → True
user_states: Dict           = {}   # user_id → state dict
acc_mgr: Optional[AccountManager] = None   # initialised in main()

# ══════════════════════════════════════════════════════════════
#  5.  DEFAULT DATA  (seeded into DB on first run)
# ══════════════════════════════════════════════════════════════
DEFAULT_SETTINGS = {
    "bot_name":         "Stark Bot",
    "upi_id":           "",
    "upi_name":         "Stark Bot",
    "support_link":     "",
    "referral_bonus":   10.0,   # ₹ given when a referred user joins
    "referral_percent": 3.0,    # % of every deposit credited to referrer
    "min_deposit":      10.0,   # minimum UPI deposit in ₹
}

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

# Country code → flag lookup (used when uploading sessions)
COUNTRY_FLAGS: Dict[str, str] = {c["code"]: c["flag"] for c in DEFAULT_COUNTRIES}
COUNTRY_FLAGS.update({
    "AU": "🇦🇺", "CA": "🇨🇦", "TR": "🇹🇷", "DE": "🇩🇪", "FR": "🇫🇷",
    "IT": "🇮🇹", "ES": "🇪🇸", "MX": "🇲🇽", "AR": "🇦🇷", "TH": "🇹🇭",
    "UA": "🇺🇦", "SA": "🇸🇦", "AE": "🇦🇪", "JP": "🇯🇵", "KR": "🇰🇷",
    "CN": "🇨🇳", "IR": "🇮🇷", "IQ": "🇮🇶", "MA": "🇲🇦", "UZ": "🇺🇿",
    "KZ": "🇰🇿", "NP": "🇳🇵", "LK": "🇱🇰", "SG": "🇸🇬", "MY": "🇲🇾",
})

# ══════════════════════════════════════════════════════════════
#  6.  DB INIT
# ══════════════════════════════════════════════════════════════
async def init_db():
    """Create indexes and seed defaults on first run."""
    await users_col.create_index("user_id", unique=True)
    await accounts_col.create_index([("country_code", 1), ("status", 1)])
    await deposits_col.create_index("user_id")
    await orders_col.create_index("user_id")
    await settings_col.create_index("key", unique=True)
    await countries_col.create_index("code", unique=True)
    await bot_admins_col.create_index("telegram_id", unique=True)

    for key, val in DEFAULT_SETTINGS.items():
        if not await settings_col.find_one({"key": key}):
            await settings_col.insert_one({"key": key, "value": val})

    for c in DEFAULT_COUNTRIES:
        if not await countries_col.find_one({"code": c["code"]}):
            await countries_col.insert_one({**c, "is_active": True})

    log.info("✅ Database initialised")


# ══════════════════════════════════════════════════════════════
#  7.  SETTINGS HELPERS  (in-memory cache, 60 s TTL)
# ══════════════════════════════════════════════════════════════
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


# ══════════════════════════════════════════════════════════════
#  8.  ADMIN HELPERS
# ══════════════════════════════════════════════════════════════
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


# ══════════════════════════════════════════════════════════════
#  9.  BOT USERNAME CACHE
# ══════════════════════════════════════════════════════════════
_bot_username: Optional[str] = None


async def get_bot_username() -> str:
    global _bot_username
    if not _bot_username:
        me = await bot.get_me()
        _bot_username = me.username
    return _bot_username or ""


# ══════════════════════════════════════════════════════════════
#  10. FORCE-JOIN HELPERS
# ══════════════════════════════════════════════════════════════
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
        return True      # can't check → don't block
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
    buttons.append([Button.inline("✅ I Joined — Check Again", b"check_join")])
    await event.respond(
        "🔒 **You must join the channel(s) below to use this bot.**",
        buttons=buttons,
    )


# ══════════════════════════════════════════════════════════════
#  11. USER HELPER
# ══════════════════════════════════════════════════════════════
async def get_or_create_user(user_id: int,
                              referrer_id: Optional[int] = None) -> dict:
    user = await users_col.find_one({"user_id": user_id})
    if not user:
        import random, string
        ref_code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        user = {
            "user_id":           user_id,
            "balance":           0.0,
            "referral_code":     ref_code,
            "referred_by":       referrer_id,
            "referral_earnings": 0.0,
            "is_banned":         False,
            "joined_at":         datetime.utcnow(),
        }
        await users_col.insert_one(user)

        # Pay join bonus to referrer
        if referrer_id and referrer_id != user_id:
            bonus = float(await get_setting("referral_bonus", 10.0))
            if bonus > 0:
                await users_col.update_one(
                    {"user_id": referrer_id},
                    {"$inc": {"balance": bonus, "referral_earnings": bonus}},
                )
                try:
                    await bot.send_message(
                        referrer_id,
                        f"🎁 **Referral Bonus!**\n"
                        f"+₹{bonus:.0f} credited — someone joined using your link!",
                    )
                except Exception:
                    pass
    return user


# ══════════════════════════════════════════════════════════════
#  12. COUNTRIES HELPER
# ══════════════════════════════════════════════════════════════
async def get_active_countries() -> List[dict]:
    result = []
    async for c in countries_col.find({"is_active": True}):
        stock = await accounts_col.count_documents(
            {"country_code": c["code"], "status": "available"}
        )
        result.append({
            "code":  c["code"],
            "name":  c["name"],
            "flag":  c["flag"],
            "price": c["price"],
            "stock": stock,
        })
    return result


# ══════════════════════════════════════════════════════════════
#  13. SESSION ZIP → STRING SESSION CONVERTER
#      (The key fix that makes OTP delivery work)
# ══════════════════════════════════════════════════════════════
def _phone_from_filename(name: str) -> str:
    """Extract phone number from a .session filename like +911234567890.session"""
    base   = os.path.splitext(os.path.basename(name))[0]
    digits = re.sub(r"[^\d]", "", base)
    return f"+{digits}" if len(digits) >= 7 else base


def _detect_session_format(session_bytes: bytes):
    """
    Peek inside the SQLite .session file and detect whether it is a
    Telethon or Pyrogram session.

    Telethon columns : dc_id, server_address, port, auth_key, takeout_id
    Pyrogram columns : dc_id, api_id, test_mode, auth_key, date, user_id, is_bot

    Returns (fmt, dc_id, server_address, port, auth_key)
    where fmt is "telethon" | "pyrogram" | None.
    """
    import sqlite3, struct, ipaddress as _ip

    # Standard Telegram DC → IPv4 map (production DCs)
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
            # ── Telethon ───────────────────────────────────────
            cur.execute(
                "SELECT dc_id, server_address, port, auth_key FROM sessions"
            )
            row = cur.fetchone()
            conn.close()
            if row and row[3]:
                return "telethon", row[0], row[1], row[2], row[3]

        elif "user_id" in cols or "api_id" in cols:
            # ── Pyrogram ──────────────────────────────────────
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
    """
    Manually build a Telethon StringSession string from raw MTProto credentials.

    Telethon StringSession wire format (version 1):
      "1" + base64url( dc_id(1B) + server_ip(4B) + port(2B) + auth_key(256B) )
    """
    import struct, base64, ipaddress as _ip
    try:
        if not isinstance(auth_key, bytes) or len(auth_key) != 256:
            log.warning(f"[pyro→ss] bad auth_key len={len(auth_key) if auth_key else None}")
            return None
        ip_bytes = _ip.ip_address(server).packed          # 4 bytes for IPv4
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
    """
    Convert raw SQLite .session file bytes → Telethon StringSession string.
    Supports BOTH Telethon and Pyrogram .session formats.

    OFFLINE — no Telegram connection is made.  We read dc_id / server / port /
    auth_key straight from the SQLite file and build the StringSession locally.
    This means sessions created with ANY API_ID (including third-party apps like
    Salaar's) are accepted — no is_user_authorized() check that would reject them.
    The live AccountManager client will verify when it connects for real.
    """
    fmt, dc_id, server, port, auth_key = _detect_session_format(session_bytes)

    if fmt is None:
        log.warning("[zip] Unknown session format — skipping")
        return None

    ss = _pyrogram_to_telethon_ss(dc_id, server, port, auth_key)
    if ss:
        log.info(f"[zip] ✅ {fmt} session → StringSession (offline conversion, no API_ID check)")
    else:
        log.warning(f"[zip] ❌ Could not build StringSession for {fmt} session (bad auth_key?)")
    return ss


async def process_session_zip(
    zip_bytes:    bytes,
    country_code: str,
    country_name: str,
    price:        float,
    twofa_password: str = "",
    prog_msg=None,          # optional Telethon Message object for live progress edits
) -> tuple:
    """
    Unzip → verify each .session → convert to StringSession → save in MongoDB.
    Supports any number of sessions; processes 3 concurrently via semaphore.
    Returns (added_count, skipped_count, failed_phones_list)
    """
    added: int        = 0
    skipped: int      = 0
    done: int         = 0          # total processed (added + skipped)
    errors: List[str] = []

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception as e:
        raise ValueError(f"Invalid ZIP: {e}")

    all_names  = [n for n in zf.namelist() if n.lower().endswith(".session")]
    total      = len(all_names)
    sem        = asyncio.Semaphore(5)   # max 5 concurrent (offline now, no Telegram limit)
    flag       = COUNTRY_FLAGS.get(country_code, "🌍")

    async def _update_progress():
        """Edit the progress message every 5 sessions so admin sees live count."""
        if prog_msg is None:
            return
        with contextlib.suppress(Exception):
            await prog_msg.edit(
                f"⚙️ **Processing sessions…**\n\n"
                f"📦 Total in ZIP: `{total}`\n"
                f"✅ Added:        `{added}`\n"
                f"⏭️ Skipped:      `{skipped}`\n"
                f"🔄 Done so far:  `{done}` / `{total}`\n\n"
                "_Converting sessions offline (no Telegram call)…_"
            )

    async def _process_one(name: str):
        nonlocal added, skipped, done

        phone = _phone_from_filename(name)

        async with sem:
            try:
                raw_bytes = zf.read(name)
            except Exception:
                errors.append(phone)
                done += 1
                return

            ss = await _session_file_to_string(raw_bytes)
            if ss is None:
                errors.append(phone)
                skipped += 1
                done    += 1
                if done % 5 == 0:
                    await _update_progress()
                return

            await accounts_col.insert_one({
                "phone":          phone,
                "session_string": ss,
                "country":        country_name,
                "country_code":   country_code,
                "country_flag":   flag,
                "price":          price,
                "twofa_password": twofa_password,
                "status":         "available",
                "added_at":       datetime.utcnow(),
            })

            # Connect client immediately so OTPs start arriving
            if acc_mgr is not None:
                with contextlib.suppress(Exception):
                    await acc_mgr.add_client(phone, ss)

            added += 1
            done  += 1
            log.info(f"[zip] ✅ Added {phone} ({country_name})  [{done}/{total}]")
            if done % 5 == 0:
                await _update_progress()

    await asyncio.gather(*[_process_one(n) for n in all_names])
    zf.close()
    return added, skipped, errors


# ══════════════════════════════════════════════════════════════
#  14. UPI QR CODE GENERATOR
# ══════════════════════════════════════════════════════════════
def _make_upi_qr(upi_id: str, amount: float, name: str) -> Optional[bytes]:
    """Generate UPI QR PNG bytes, or return None so deposit can use text fallback."""
    try:
        import qrcode as qrc

        uri = (f"upi://pay?pa={upi_id}&pn={name}"
               f"&am={amount:.2f}&cu=INR&tn=StarkBotDeposit")
        buf = io.BytesIO()
        qrc.make(uri).save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        log.warning(f"[QR] Failed to generate UPI QR: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  15. KEYBOARD BUILDERS
# ══════════════════════════════════════════════════════════════
async def main_menu_buttons(user_id: int) -> list:
    rows = [
        [Button.inline("🛒 Buy Account",  b"buy")],
        [Button.inline("💰 My Balance",   b"balance"),
         Button.inline("📋 My Orders",    b"orders")],
        [Button.inline("💳 Deposit",      b"deposit"),
         Button.inline("📜 History",      b"history")],
        [Button.inline("👥 Referral",     b"referral")],
    ]
    support = await get_setting("support_link")
    if support:
        rows.append([Button.url("📞 Support", support)])
    if await is_admin(user_id):
        rows.append([Button.inline("⚙️ Admin Panel", b"admin")])
    return rows


def _country_buy_buttons(countries: List[dict]) -> list:
    rows = []
    for i in range(0, len(countries), 2):
        row = []
        for c in countries[i:i + 2]:
            s = c.get("stock", 0)
            dot = "🟢" if s > 3 else ("🟡" if s > 1 else "🔴")
            row.append(Button.inline(
                f"{c['flag']} {c['name']}  {dot}  ₹{c['price']:.0f}",
                f"buy_country:{c['code']}".encode(),
            ))
        rows.append(row)
    rows.append([Button.inline("🏠 Main Menu", b"main_menu")])
    return rows


def _admin_menu_buttons(is_owner_flag: bool) -> list:
    rows = [
        [Button.inline("📊 Stats",             b"astats")],
        [Button.inline("📦 Upload Sessions",   b"upload_sessions"),
         Button.inline("📋 Session Overview",  b"manage_sessions")],
        [Button.inline("💳 Pending Deposits",  b"pending_deposits")],
        [Button.inline("📢 Broadcast",         b"broadcast")],
        [Button.inline("⚙️ Settings",          b"asettings"),
         Button.inline("🌍 Countries",         b"acountries")],
        [Button.inline("👤 Users",             b"ausers")],
    ]
    if is_owner_flag:
        rows.append([Button.inline("🔑 Manage Admins", b"manage_admins")])
    rows.append([Button.inline("🏠 Main Menu", b"main_menu")])
    return rows


# ══════════════════════════════════════════════════════════════
#  16. /start COMMAND
# ══════════════════════════════════════════════════════════════
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

    bot_name = await get_setting("bot_name", "Stark Bot")
    welcome  = (
        f"👋 **Welcome to {bot_name}!**\n\n"
        "🔐 **Buy Telegram Accounts** — get login OTP & 2FA password instantly.\n"
        "💳 **Deposit via UPI** — quick and easy.\n"
        "🌍 **Multiple Countries** — choose your country and price.\n\n"
        "Use the buttons below to get started. 👇"
    )
    await event.respond(welcome, buttons=await main_menu_buttons(user_id))


# ══════════════════════════════════════════════════════════════
#  17. CALLBACK QUERY ROUTER
# ══════════════════════════════════════════════════════════════
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

    # ── MAIN MENU ────────────────────────────────────────────
    if data == "main_menu":
        user_states.pop(user_id, None)
        bot_name = await get_setting("bot_name", "Stark Bot")
        await event.edit(
            f"🏠 **{bot_name}**\n\nChoose an option:",
            buttons=await main_menu_buttons(user_id),
        )

    # ── CHECK JOIN ───────────────────────────────────────────
    elif data == "check_join":
        if await is_user_member(user_id):
            await event.answer("✅ Verified!")
            bot_name = await get_setting("bot_name", "Stark Bot")
            await event.edit(
                f"👋 **Welcome to {bot_name}!**\n\nUse the buttons below.",
                buttons=await main_menu_buttons(user_id),
            )
        else:
            await event.answer("❌ You haven't joined yet!", alert=True)

    # ── BALANCE ──────────────────────────────────────────────
    elif data == "balance":
        if not user:
            user = await get_or_create_user(user_id)
        bal   = float(user.get("balance", 0))
        spent = 0.0
        async for o in orders_col.find(
                {"user_id": user_id, "status": {"$nin": ["cancelled"]}}):
            spent += float(o.get("amount", 0))
        await event.edit(
            f"💰 **Your Wallet**\n\n"
            f"Available Balance: `₹{bal:.2f}`\n"
            f"Total Spent:       `₹{spent:.2f}`\n\n"
            "Tap **Deposit** to add funds.",
            buttons=[
                [Button.inline("💳 Deposit Now", b"deposit")],
                [Button.inline("🏠 Main Menu",   b"main_menu")],
            ],
        )

    # ── BUY — country list ───────────────────────────────────
    elif data == "buy":
        countries = await get_active_countries()
        in_stock  = [c for c in countries if c["stock"] > 0]
        if not in_stock:
            await event.edit(
                "😔 **No accounts available right now.**\n\nCheck back soon or contact support.",
                buttons=[[Button.inline("🏠 Main Menu", b"main_menu")]],
            )
            return
        lines = ["📌 **Available Accounts**\n"]
        for c in in_stock:
            s   = c["stock"]
            dot = "🟢" if s > 3 else ("🟡" if s > 1 else "🔴")
            lines.append(
                f"{c['flag']} **{c['name']}** — {dot} {s} in stock — ₹{c['price']:.0f}"
            )
        lines.append("\n👇 Select a country to purchase:")
        await event.edit("\n".join(lines), buttons=_country_buy_buttons(in_stock))

    # ── BUY — country selected ───────────────────────────────
    elif data.startswith("buy_country:"):
        country_code = data.split(":")[1]
        country      = await countries_col.find_one({"code": country_code, "is_active": True})
        if not country:
            await event.answer("❌ Country unavailable.", alert=True)
            return
        stock = await accounts_col.count_documents(
            {"country_code": country_code, "status": "available"}
        )
        if stock == 0:
            await event.answer("❌ Out of stock!", alert=True)
            return
        if not user:
            user = await get_or_create_user(user_id)
        bal   = float(user.get("balance", 0))
        price = float(country["price"])
        dot   = "🟢" if stock > 3 else ("🟡" if stock > 1 else "🔴")
        text  = (
            f"⚡ **Account Summary**\n\n"
            f"🌍 Country:      {country['flag']} **{country['name']}**\n"
            f"📦 Stock:        {dot} {stock} available\n"
            f"💰 Price:        `₹{price:.2f}`\n"
            f"💎 Your Balance: `₹{bal:.2f}`\n"
        )
        if bal < price:
            needed = price - bal
            text  += (
                f"\n❌ **Insufficient Balance**\n"
                f"You need ₹{needed:.2f} more.  Please deposit first."
            )
            await event.edit(text, buttons=[
                [Button.inline("💳 Deposit Now", b"deposit")],
                [Button.inline("◀️ Back",        b"buy")],
            ])
        else:
            text += "\n⚠️ _Use responsibly.  Not liable for account bans._"
            await event.edit(text, buttons=[
                [Button.inline(
                    f"✅ Confirm Buy — ₹{price:.0f}",
                    f"confirm_buy:{country_code}".encode(),
                )],
                [Button.inline("◀️ Back", b"buy")],
            ])

    # ── CONFIRM BUY ──────────────────────────────────────────
    elif data.startswith("confirm_buy:"):
        country_code = data.split(":")[1]
        country      = await countries_col.find_one({"code": country_code, "is_active": True})
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

        # Atomically pick and mark one available session as sold
        session_doc = await accounts_col.find_one_and_update(
            {"country_code": country_code, "status": "available"},
            {"$set": {
                "status":   "sold",
                "buyer_id": user_id,
                "sold_at":  datetime.utcnow(),
            }},
        )
        if not session_doc:
            await event.answer("❌ Out of stock — someone just bought the last one!", alert=True)
            return

        # Deduct balance
        await users_col.update_one({"user_id": user_id}, {"$inc": {"balance": -price}})

        # Pay referral % to referrer
        if user.get("referred_by"):
            pct   = float(await get_setting("referral_percent", 3.0))
            bonus = round(price * pct / 100, 2)
            if bonus > 0:
                await users_col.update_one(
                    {"user_id": user["referred_by"]},
                    {"$inc": {"balance": bonus, "referral_earnings": bonus}},
                )
                try:
                    await bot.send_message(
                        user["referred_by"],
                        f"💸 **Referral Earning!**\n"
                        f"+₹{bonus:.2f} from your referral's purchase.",
                    )
                except Exception:
                    pass

        phone = session_doc["phone"]
        twofa = session_doc.get("twofa_password", "")

        # Record order
        await orders_col.insert_one({
            "user_id":      user_id,
            "phone":        phone,
            "country":      country["name"],
            "country_code": country_code,
            "country_flag": country.get("flag", ""),
            "amount":       price,
            "twofa":        twofa,
            "status":       "waiting_otp",
            "created_at":   datetime.utcnow(),
        })

        pending_otp_requests[(user_id, phone)] = True

        msg = (
            f"✅ **Purchase Successful!**\n\n"
            f"🌍 Country: {country.get('flag','')} {country['name']}\n"
            f"📞 Phone: `{phone}`\n"
        )
        if twofa:
            msg += f"🔐 **2FA Password:** `{twofa}`\n"
        msg += (
            "\n⏳ **OTP will arrive automatically** when Telegram sends it.\n"
            "Or tap **Request OTP** to trigger a fresh code right now.\n\n"
            "⚠️ Use responsibly."
        )
        await event.edit(msg, buttons=[
            [Button.inline("📩 Request OTP", f"resend_{phone}".encode())],
            [Button.inline("🏠 Main Menu",   b"main_menu")],
        ])

    # ── REQUEST / RESEND OTP ─────────────────────────────────
    elif data.startswith("resend_"):
        phone = data[7:]
        await event.answer("⏳ Requesting OTP…", alert=False)
        success = await acc_mgr.request_otp(phone)
        if success:
            await event.respond(
                f"📤 OTP request sent for `{phone}`.\n"
                "It will appear here the moment Telegram delivers it."
            )
        else:
            await event.respond(
                f"❌ Could not trigger OTP for `{phone}`.\n"
                "Session may be expired.  Contact support."
            )

    # ── LOGOUT FROM BOT ──────────────────────────────────────
    elif data.startswith("logout_"):
        phone = data[7:]
        await acc_mgr.logout_client(phone)
        # find_one_and_update to safely target the most-recent sold record
        await accounts_col.find_one_and_update(
            {"phone": phone, "status": "sold"},
            {"$set": {"status": "logged_out", "logged_out_at": datetime.utcnow()}},
            sort=[("sold_at", -1)],
        )
        await event.answer("✅ Logged out.", alert=False)
        await event.edit(
            f"🔓 **Logged Out**\n\n"
            f"`{phone}` has been disconnected from the bot.\n"
            "The account is no longer receiving messages.",
            buttons=[[Button.inline("🏠 Main Menu", b"main_menu")]],
        )

    # ── MY ORDERS ────────────────────────────────────────────
    elif data == "orders":
        docs = await orders_col.find(
            {"user_id": user_id}
        ).sort("created_at", -1).limit(8).to_list(8)
        if not docs:
            await event.edit(
                "📋 **My Orders**\n\n_No orders yet._\n\nBuy an account to get started.",
                buttons=[[Button.inline("🛒 Buy Now", b"buy"),
                          Button.inline("🏠 Menu",    b"main_menu")]],
            )
            return
        _STATUS = {
            "waiting_otp": "⏳ Waiting OTP",
            "completed":   "✅ Completed",
            "cancelled":   "❌ Cancelled",
            "logged_out":  "🔓 Logged Out",
        }
        lines = ["📋 **My Orders — Last 8**\n"]
        for i, o in enumerate(docs, 1):
            st   = _STATUS.get(o.get("status", ""), o.get("status", "").title())
            line = (
                f"**{i}.** {o.get('country_flag','')} **{o.get('country','?')}**  —  {st}\n"
                f"📱 `{o.get('phone','?')}`  •  ₹{o.get('amount',0):.0f}"
            )
            if o.get("twofa"):
                line += f"\n🔐 2FA: `{o['twofa']}`"
            lines.append(line)
        kb = [[Button.inline("🏠 Main Menu", b"main_menu")]]
        if docs[0].get("status") == "waiting_otp":
            kb.insert(0, [Button.inline(
                "📩 Re-request OTP",
                f"resend_{docs[0]['phone']}".encode(),
            )])
        await event.edit("\n\n".join(lines), buttons=kb)

    # ── HISTORY ──────────────────────────────────────────────
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
            "📋 **History**\n",
            f"💸 Total Spent:     `₹{spent:.2f}`",
            f"💰 Total Deposited: `₹{dep_tot:.2f}`",
        ]
        if orders:
            lines.append("\n🛒 **Recent Purchases:**")
            for o in orders:
                lines.append(
                    f"• {o.get('country_flag','')} {o.get('country','?')}"
                    f" — `₹{o.get('amount',0):.0f}` — `{o.get('phone','?')}`"
                )
        if deps:
            lines.append("\n💰 **Recent Deposits:**")
            _DE = {"approved": "✅", "pending": "⏳", "rejected": "❌"}
            for d in deps:
                lines.append(
                    f"{_DE.get(d.get('status',''),'•')} "
                    f"₹{d.get('amount',0):.0f} — {d.get('status','').title()}"
                )
        await event.edit(
            "\n".join(lines),
            buttons=[[Button.inline("🏠 Main Menu", b"main_menu")]],
        )

    # ── DEPOSIT ──────────────────────────────────────────────
    elif data == "deposit":
        upi_id   = await get_setting("upi_id", "")
        upi_name = await get_setting("upi_name", "Stark Bot")
        min_dep  = float(await get_setting("min_deposit", 10.0))
        if not upi_id:
            await event.edit(
                "⚠️ Deposit is currently unavailable.\nContact support.",
                buttons=[[Button.inline("🏠 Main Menu", b"main_menu")]],
            )
            return
        user_states[user_id] = {
            "state":    "deposit_amount",
            "upi_id":   upi_id,
            "upi_name": upi_name,
        }
        await event.edit(
            f"💳 **Deposit via UPI**\n\n"
            f"Minimum deposit: **₹{min_dep:.0f}**\n\n"
            "**Step 1 — Send the amount you want to deposit (₹):**",
            buttons=[[Button.inline("❌ Cancel", b"main_menu")]],
        )

    # ── REFERRAL ─────────────────────────────────────────────
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
            f"🎁 **Refer & Earn**\n\n"
            f"Earn **{pct:.1f}%** on every deposit your referral makes!\n"
            f"Plus **₹{bonus:.0f}** instant join bonus!\n\n"
            f"🔗 **Your Referral Link:**\n`{ref_link}`\n\n"
            f"👥 Total Referred:   **{count}**\n"
            f"💰 Total Earned:     `₹{earnings:.2f}`\n\n"
            "**How it works:**\n"
            "1️⃣ Share your link\n"
            "2️⃣ Friend joins & deposits\n"
            f"3️⃣ You get **{pct:.1f}%** of every deposit + ₹{bonus:.0f} join bonus!",
            buttons=[
                [Button.url(
                    "📤 Share My Link",
                    f"https://t.me/share/url?url={ref_link}"
                    "&text=Buy+Telegram+accounts+instantly!",
                )],
                [Button.inline("🏠 Main Menu", b"main_menu")],
            ],
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
            "⚙️ **Admin Panel**\n\n"
            + ("👑 Owner Mode — Full Access\n\n" if owner_flag else "")
            + "Select an action:",
            buttons=_admin_menu_buttons(owner_flag),
        )

    # ── STATS ────────────────────────────────────────────────
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
            f"📊 **Bot Statistics**\n\n"
            f"👥 Total Users:   `{total_users}` (+{new_today} today)\n"
            f"🚫 Banned:        `{banned}`\n"
            f"🔑 Extra Admins:  `{db_admins}`\n\n"
            f"📦 Total Sessions: `{total_acc}`\n"
            f"✅ Available:      `{avail_acc}`\n"
            f"🛒 Total Orders:   `{total_orders}`\n\n"
            f"💳 Pending Deposits: `{pending_deps}`\n"
            f"💰 Total Revenue:    `₹{revenue:.2f}`",
            buttons=[[Button.inline("◀️ Back", b"admin")]],
        )

    # ── UPLOAD SESSIONS — pick country ───────────────────────
    elif data == "upload_sessions":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        c_list = await countries_col.find({"is_active": True}).to_list(50)
        rows   = []
        for i in range(0, len(c_list), 2):
            row = []
            for c in c_list[i:i + 2]:
                row.append(Button.inline(
                    f"{c['flag']} {c['name']}",
                    f"upload_country:{c['code']}".encode(),
                ))
            rows.append(row)
        rows.append([Button.inline("◀️ Back", b"admin")])
        await event.edit("📦 **Upload Sessions**\n\nSelect country:", buttons=rows)

    elif data.startswith("upload_country:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        cc = data.split(":")[1]
        c  = await countries_col.find_one({"code": cc})
        user_states[user_id] = {
            "state":        "waiting_zip",
            "country_code": cc,
            "country_name": c["name"],
            "price":        float(c["price"]),
            "twofa_password": "",
        }
        await event.edit(
            f"📦 **Upload Sessions — {c['flag']} {c['name']}**\n\n"
            "**Step 1 (optional):** If these accounts have a 2FA password,\n"
            "send the password as a text message first.\n\n"
            "**Step 2:** Send the **.zip file** containing `.session` files.\n\n"
            "The bot will automatically:\n"
            "✅ Verify each session (check if authorised)\n"
            "✅ Convert to StringSession (for live OTP delivery)\n"
            "✅ Connect each account immediately\n\n"
            "If no 2FA, just send the ZIP directly.",
            buttons=[[Button.inline("❌ Cancel", b"admin")]],
        )

    # ── SESSION OVERVIEW ─────────────────────────────────────
    elif data == "manage_sessions":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        total   = await accounts_col.count_documents({})
        avail   = await accounts_col.count_documents({"status": "available"})
        sold    = await accounts_col.count_documents({"status": "sold"})
        logout_ = await accounts_col.count_documents({"status": "logged_out"})
        # Group by country
        pipe = await accounts_col.aggregate([
            {"$match": {"status": "available"}},
            {"$group": {"_id": "$country", "count": {"$sum": 1}, "flag": {"$first": "$country_flag"}}},
            {"$sort": {"count": -1}},
        ]).to_list(20)
        by_country = "\n".join(
            f"  {r.get('flag','🌍')} {r['_id']}: `{r['count']}`"
            for r in pipe
        ) or "  (empty)"
        await event.edit(
            f"📋 **Session Overview**\n\n"
            f"Total:       `{total}`\n"
            f"✅ Available: `{avail}`\n"
            f"🔑 Sold:      `{sold}`\n"
            f"🔓 Logged Out:`{logout_}`\n\n"
            f"**Available by Country:**\n{by_country}",
            buttons=[[Button.inline("◀️ Back", b"admin")]],
        )

    # ── PENDING DEPOSITS ─────────────────────────────────────
    elif data == "pending_deposits":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        deps = await deposits_col.find(
            {"status": "pending"}).sort("created_at", 1).limit(10).to_list(10)
        if not deps:
            await event.edit(
                "✅ No pending deposits.",
                buttons=[[Button.inline("◀️ Back", b"admin")]],
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
                f"💳 **Deposit Request**\n"
                f"User ID:  `{uid}`\n"
                f"Amount:   `₹{amount:.2f}`\n"
                f"Time:     {created}",
                buttons=[
                    [Button.inline(
                        "✅ Approve",
                        f"dep_approve:{dep_id}:{uid}:{amount}".encode(),
                    ),
                    Button.inline(
                        "❌ Reject",
                        f"dep_reject:{dep_id}:{uid}".encode(),
                    )],
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

        # Claim the deposit atomically so two admins cannot approve it twice.
        claim = await deposits_col.update_one(
            {"_id": ObjectId(dep_id), "status": "pending"},
            {"$set": {
                "status":      "approved",
                "approved_at": datetime.utcnow(),
                "approved_by": user_id,
            }},
        )
        if claim.matched_count == 0:
            await event.answer("⚠️ Already processed by another admin.", alert=True)
            await event.edit("⏭️ This deposit was already processed by another admin.")
            return

        dep_doc = await deposits_col.find_one({"_id": ObjectId(dep_id)})
        if dep_doc:
            uid    = int(dep_doc.get("user_id", uid))
            amount = float(dep_doc.get("amount", amount))

        # Pay referral % on deposit to referrer
        buyer = await users_col.find_one({"user_id": uid})
        if buyer and buyer.get("referred_by"):
            pct   = float(await get_setting("referral_percent", 3.0))
            bonus = round(amount * pct / 100, 2)
            if bonus > 0:
                await users_col.update_one(
                    {"user_id": buyer["referred_by"]},
                    {"$inc": {"balance": bonus, "referral_earnings": bonus}},
                )
                try:
                    await bot.send_message(
                        buyer["referred_by"],
                        f"💸 **Referral Earning!**\n"
                        f"+₹{bonus:.2f} from your referral's deposit.",
                    )
                except Exception:
                    pass

        await users_col.update_one({"user_id": uid}, {"$inc": {"balance": amount}})
        try:
            await bot.send_message(
                uid,
                f"✅ **Deposit Approved!**\n`₹{amount:.2f}` added to your wallet.",
            )
        except Exception:
            pass
        await event.edit(f"✅ Approved ₹{amount:.2f} for user `{uid}`.")

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
            await event.answer("⚠️ Already processed by another admin.", alert=True)
            await event.edit("⏭️ This deposit was already processed by another admin.")
            return
        uid = int(dep_doc.get("user_id", uid))
        try:
            await bot.send_message(
                uid,
                "❌ **Deposit Rejected.**\n"
                "Contact support if this is a mistake.",
            )
        except Exception:
            pass
        await event.edit(f"❌ Rejected deposit for user `{uid}`.")

    # ── BROADCAST ────────────────────────────────────────────
    elif data == "broadcast":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "broadcast"}
        await event.edit(
            "📢 **Broadcast**\n\n"
            "Send the message to broadcast to all users.\n"
            "(Text only — keep it short!)",
            buttons=[[Button.inline("❌ Cancel", b"admin")]],
        )

    # ── SETTINGS ─────────────────────────────────────────────
    elif data == "asettings":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        upi    = await get_setting("upi_id",         "Not set")
        uname_ = await get_setting("upi_name",        "Stark Bot")
        sup    = await get_setting("support_link",    "Not set")
        bn     = await get_setting("bot_name",        "Stark Bot")
        rb     = await get_setting("referral_bonus",  10)
        rp     = await get_setting("referral_percent", 3)
        md     = await get_setting("min_deposit",     10)
        await event.edit(
            f"⚙️ **Settings**\n\n"
            f"🤖 Bot Name:      `{bn}`\n"
            f"💳 UPI ID:        `{upi}`\n"
            f"👤 UPI Name:      `{uname_}`\n"
            f"📞 Support Link:  `{sup}`\n"
            f"🎁 Ref Bonus:     `₹{rb}`\n"
            f"📈 Ref %:         `{rp}%`\n"
            f"🔢 Min Deposit:   `₹{md}`",
            buttons=[
                [Button.inline("🤖 Bot Name",    b"set_botname"),
                 Button.inline("💳 UPI ID",      b"set_upi")],
                [Button.inline("👤 UPI Name",    b"set_upiname"),
                 Button.inline("📞 Support",     b"set_support")],
                [Button.inline("🎁 Ref Bonus",   b"set_ref_bonus"),
                 Button.inline("📈 Ref %",       b"set_ref_pct")],
                [Button.inline("🔢 Min Deposit", b"set_min_dep")],
                [Button.inline("◀️ Back",        b"admin")],
            ],
        )

    elif data in (
        "set_botname", "set_upi", "set_upiname", "set_support",
        "set_ref_bonus", "set_ref_pct", "set_min_dep",
    ):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        _prompts = {
            "set_botname":   ("setting_botname",  "🤖 Send the new **bot name**:"),
            "set_upi":       ("setting_upi",       "💳 Send the new **UPI ID** (e.g. name@upi):"),
            "set_upiname":   ("setting_upiname",   "👤 Send the UPI **display name**:"),
            "set_support":   ("setting_support",   "📞 Send the **support link** (e.g. https://t.me/youruser):"),
            "set_ref_bonus": ("setting_ref_bonus", "🎁 Send **referral join bonus** in ₹ (e.g. 10):"),
            "set_ref_pct":   ("setting_ref_pct",   "📈 Send **referral deposit %** (e.g. 3):"),
            "set_min_dep":   ("setting_min_dep",   "🔢 Send **minimum deposit** in ₹ (e.g. 50):"),
        }
        sk, prompt = _prompts[data]
        user_states[user_id] = {"state": sk}
        await event.edit(prompt, buttons=[[Button.inline("❌ Cancel", b"asettings")]])

    # ── COUNTRIES ────────────────────────────────────────────
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
        rows.append([Button.inline("➕ Add Country", b"add_country"),
                     Button.inline("◀️ Back",        b"admin")])
        await event.edit(
            "🌍 **Countries** (tap to enable / disable):",
            buttons=rows,
        )

    elif data.startswith("ctoggle:"):
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        code = data.split(":")[1]
        c    = await countries_col.find_one({"code": code})
        new  = not c.get("is_active", True)
        await countries_col.update_one({"code": code}, {"$set": {"is_active": new}})
        await event.answer(f"{'Enabled' if new else 'Disabled'} {code}")
        c_list = await countries_col.find({}).to_list(50)
        rows   = []
        for c2 in c_list:
            em = "✅" if c2.get("is_active") else "❌"
            rows.append([Button.inline(
                f"{em} {c2['flag']} {c2['name']} — ₹{c2['price']:.0f}",
                f"ctoggle:{c2['code']}".encode(),
            )])
        rows.append([Button.inline("➕ Add Country", b"add_country"),
                     Button.inline("◀️ Back",        b"admin")])
        await event.edit("🌍 **Countries**:", buttons=rows)

    elif data == "add_country":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "add_country"}
        await event.edit(
            "🌍 **Add / Update Country**\n\n"
            "Send in this format:\n"
            "`CODE | Country Name | Flag | Price`\n\n"
            "Example:\n`TR | Turkey | 🇹🇷 | 28`",
            buttons=[[Button.inline("❌ Cancel", b"acountries")]],
        )

    # ── USERS ────────────────────────────────────────────────
    elif data == "ausers":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "search_user"}
        await event.edit(
            "👤 **User Lookup**\n\nSend the user's Telegram ID:",
            buttons=[
                [Button.inline("➕ Add Balance", b"admin_add_bal"),
                 Button.inline("🚫 Ban/Unban",   b"admin_ban")],
                [Button.inline("◀️ Back",        b"admin")],
            ],
        )

    elif data == "admin_add_bal":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "add_bal_uid"}
        await event.edit(
            "💰 **Add Balance**\n\nSend the user's Telegram ID:",
            buttons=[[Button.inline("❌ Cancel", b"admin")]],
        )

    elif data == "admin_ban":
        if not await is_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return
        user_states[user_id] = {"state": "ban_uid"}
        await event.edit(
            "🚫 **Ban / Unban User**\n\nSend the user's Telegram ID:",
            buttons=[[Button.inline("❌ Cancel", b"admin")]],
        )

    # ── MANAGE ADMINS (owner only) ───────────────────────────
    elif data == "manage_admins":
        if user_id != OWNER_ID:
            await event.answer("❌ Owner only!", alert=True)
            return
        admins = await bot_admins_col.find({"is_active": True}).to_list(50)
        rows   = []
        for a in admins:
            rows.append([Button.inline(
                f"🔴 Remove {a.get('name', a['telegram_id'])}",
                f"rm_admin:{a['telegram_id']}".encode(),
            )])
        rows.append([Button.inline("➕ Add Admin", b"add_admin"),
                     Button.inline("◀️ Back",      b"admin")])
        await event.edit(
            f"🔑 **Manage Admins**\n\n"
            f"Owner (you): `{OWNER_ID}`\n"
            f"Extra admins: {len(admins)}",
            buttons=rows,
        )

    elif data == "add_admin":
        if user_id != OWNER_ID:
            await event.answer("❌ Owner only!", alert=True)
            return
        user_states[user_id] = {"state": "add_admin"}
        await event.edit(
            "🔑 **Add Admin**\n\nSend the Telegram user ID to grant admin access:",
            buttons=[[Button.inline("❌ Cancel", b"manage_admins")]],
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
            f"🔴 Remove {a.get('name', a['telegram_id'])}",
            f"rm_admin:{a['telegram_id']}".encode(),
        )] for a in admins]
        rows.append([Button.inline("➕ Add Admin", b"add_admin"),
                     Button.inline("◀️ Back",      b"admin")])
        await event.edit("🔑 **Manage Admins**:", buttons=rows)

    else:
        await event.answer()


# ══════════════════════════════════════════════════════════════
#  18. TEXT / FILE MESSAGE HANDLER  (state machine)
# ══════════════════════════════════════════════════════════════
@bot.on(events.NewMessage())
async def message_handler(event):
    # Ignore commands (handled separately)
    text = event.message.text or ""
    if text.startswith("/"):
        return

    user_id    = event.sender_id
    state_data = user_states.get(user_id)
    if not state_data:
        return

    state = state_data.get("state") if isinstance(state_data, dict) else state_data

    # ── DEPOSIT: user sends amount ────────────────────────────
    if state == "deposit_amount":
        try:
            amount = float(text.strip().replace(",", ""))
        except ValueError:
            await event.respond("❌ Please send a valid number. Example: `200`")
            return
        min_d = float(await get_setting("min_deposit", 10.0))
        if amount < min_d:
            await event.respond(f"❌ Minimum deposit is ₹{min_d:.0f}.")
            return
        upi_id   = state_data.get("upi_id", "")
        upi_name = state_data.get("upi_name", "Stark Bot")
        user_states[user_id] = {
            "state":    "deposit_screenshot",
            "amount":   amount,
            "upi_id":   upi_id,
            "upi_name": upi_name,
        }
        payment_message = (
            "🇮🇳 **UPI Payment**\n\n"
            f"💰 **Amount: ₹{amount:.1f}**\n\n"
            "📲 Scan the QR Code above using any UPI App "
            "(GPay, PhonePe, Paytm).\n\n"
            f"💳 Scan QR or use UPI ID: `{upi_id}`\n\n"
            "✅ Payment karne ke baad uska **screenshot yahan bhejo**."
        )
        qr = _make_upi_qr(upi_id, amount, upi_name)
        if qr:
            qr_file = io.BytesIO(qr)
            qr_file.name = "upi_payment_qr.png"
            await event.respond(
                file=qr_file,
                message=payment_message,
                force_document=False,
                buttons=[[Button.inline("❌ Cancel", b"main_menu")]],
            )
        else:
            await event.respond(
                payment_message
                + f"\n\nUPI ID: `{upi_id}`\nName: **{upi_name}**",
                buttons=[[Button.inline("❌ Cancel", b"main_menu")]],
            )

    # ── DEPOSIT: user sends payment screenshot ────────────────
    elif state in ("deposit_payment", "deposit_screenshot"):
        amount = state_data["amount"]

        if not event.message.photo:
            await event.respond(
                "📸 Please send your **payment screenshot as a photo**.\n\n"
                "Payment karne ke baad UPI transaction ka screenshot yahan bhejo.",
                buttons=[[Button.inline("❌ Cancel", b"main_menu")]],
            )
            return

        req_id = str(uuid.uuid4())[:8].upper()
        await deposits_col.insert_one({
            "request_id": req_id,
            "user_id":    user_id,
            "amount":     amount,
            "method":     "upi",
            "status":     "pending",
            "created_at": datetime.utcnow(),
        })
        dep_doc = await deposits_col.find_one({"request_id": req_id})
        dep_id  = str(dep_doc["_id"])
        user_states.pop(user_id, None)

        # Confirm to user
        await event.respond(
            f"✅ **Deposit Submitted!**\n\n"
            f"Amount: `₹{amount:.0f}`\n"
            f"Ref:    `{req_id}`\n\n"
            "⏳ Admin screenshot verify karke wallet mein amount credit karega.",
            buttons=[[Button.inline("🏠 Main Menu", b"main_menu")]],
        )

        # Download the screenshot bytes to forward to admins, when supplied.
        try:
            photo_bytes = (
                await event.message.download_media(bytes)
                if event.message.photo
                else None
            )
        except Exception:
            photo_bytes = None

        admin_caption = (
            "💳 **New Deposit — Screenshot**\n\n"
            f"User:   `{user_id}`\n"
            f"Amount: `₹{amount:.0f}`\n"
            f"Ref:    `{req_id}`"
        )
        approve_reject_btns = [
            [Button.inline(
                "✅ Approve",
                f"dep_approve:{dep_id}:{user_id}:{amount}".encode(),
            ),
            Button.inline(
                "❌ Reject",
                f"dep_reject:{dep_id}:{user_id}".encode(),
            )],
        ]

        for admin_id in await get_all_admin_ids():
            try:
                if photo_bytes:
                    # Send screenshot + approve/reject buttons together
                    screenshot_file = io.BytesIO(photo_bytes)
                    screenshot_file.name = "payment_screenshot.jpg"
                    await bot.send_file(
                        admin_id,
                        file=screenshot_file,
                        caption=admin_caption,
                        force_document=False,
                        buttons=approve_reject_btns,
                    )
                else:
                    await bot.send_message(
                        admin_id,
                        admin_caption,
                        buttons=approve_reject_btns,
                    )
            except Exception:
                pass

    # ── ADMIN: 2FA password before ZIP ───────────────────────
    elif state == "waiting_zip" and not event.message.file:
        twofa = text.strip()
        user_states[user_id] = {**state_data, "twofa_password": twofa}
        await event.respond(
            f"🔐 2FA password saved: `{twofa}`\n\n"
            "Now send the **.zip file** containing the `.session` files.",
            buttons=[[Button.inline("❌ Cancel", b"admin")]],
        )

    # ── ADMIN: ZIP file ───────────────────────────────────────
    elif state == "waiting_zip" and event.message.file:
        prog = await event.respond("⏳ Downloading ZIP…")
        try:
            zip_bytes = await event.message.download_media(bytes)
        except Exception as e:
            await prog.edit(f"❌ Download failed: {e}")
            return

        # Count .session files in ZIP before starting
        try:
            _zf_peek = zipfile.ZipFile(io.BytesIO(zip_bytes))
            total_in_zip = sum(
                1 for n in _zf_peek.namelist() if n.lower().endswith(".session")
            )
            _zf_peek.close()
        except Exception:
            total_in_zip = "?"

        await prog.edit(
            f"⚙️ **Processing sessions…**\n\n"
            f"📦 Found: `{total_in_zip}` session files\n"
            f"🔄 Converting sessions (offline, no Telegram check)…\n\n"
            "_This updates every 5 sessions. Please wait._"
        )
        cc    = state_data["country_code"]
        cname = state_data["country_name"]
        price = state_data["price"]
        twofa = state_data.get("twofa_password", "")
        try:
            added, skipped, errors = await process_session_zip(
                zip_bytes, cc, cname, price, twofa, prog
            )
        except ValueError as e:
            await prog.edit(f"❌ {e}")
            user_states.pop(user_id, None)
            return
        flag = COUNTRY_FLAGS.get(cc, "🌍")
        result = (
            f"📦 **Upload Complete** — {flag} {cname}\n\n"
            f"✅ Added & connected: `{added}`\n"
            f"⏭️ Skipped (invalid): `{skipped}`\n"
        )
        if errors:
            shown = errors[:10]
            result += f"❌ Failed ({len(errors)}): `{', '.join(shown)}`"
        await prog.edit(result, buttons=[[Button.inline("◀️ Admin", b"admin")]])
        user_states.pop(user_id, None)

    # ── ADMIN: BROADCAST ─────────────────────────────────────
    elif state == "broadcast":
        user_states.pop(user_id, None)
        prog  = await event.respond("📢 Broadcasting…")
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
                    await prog.edit(f"📢 Broadcasting… {count} sent, {fail} failed")
                except Exception:
                    pass
            await asyncio.sleep(0.05)
        await prog.edit(f"✅ Broadcast complete — {count} sent, {fail} failed.")

    # ── ADMIN: SETTING VALUES ─────────────────────────────────
    elif state == "setting_botname":
        await set_setting("bot_name", text.strip())
        user_states.pop(user_id, None)
        await event.respond("✅ Bot name updated.",
                            buttons=[[Button.inline("◀️ Settings", b"asettings")]])

    elif state == "setting_upi":
        await set_setting("upi_id", text.strip())
        user_states.pop(user_id, None)
        await event.respond("✅ UPI ID updated.",
                            buttons=[[Button.inline("◀️ Settings", b"asettings")]])

    elif state == "setting_upiname":
        await set_setting("upi_name", text.strip())
        user_states.pop(user_id, None)
        await event.respond("✅ UPI name updated.",
                            buttons=[[Button.inline("◀️ Settings", b"asettings")]])

    elif state == "setting_support":
        await set_setting("support_link", text.strip())
        user_states.pop(user_id, None)
        await event.respond("✅ Support link updated.",
                            buttons=[[Button.inline("◀️ Settings", b"asettings")]])

    elif state == "setting_ref_bonus":
        try:
            val = float(text.strip())
            await set_setting("referral_bonus", val)
            user_states.pop(user_id, None)
            await event.respond(f"✅ Referral bonus set to ₹{val:.0f}.",
                                buttons=[[Button.inline("◀️ Settings", b"asettings")]])
        except ValueError:
            await event.respond("❌ Send a valid number (e.g. `10`).")

    elif state == "setting_ref_pct":
        try:
            val = float(text.strip())
            await set_setting("referral_percent", val)
            user_states.pop(user_id, None)
            await event.respond(f"✅ Referral % set to {val:.1f}%.",
                                buttons=[[Button.inline("◀️ Settings", b"asettings")]])
        except ValueError:
            await event.respond("❌ Send a valid number (e.g. `3`).")

    elif state == "setting_min_dep":
        try:
            val = float(text.strip())
            await set_setting("min_deposit", val)
            user_states.pop(user_id, None)
            await event.respond(f"✅ Minimum deposit set to ₹{val:.0f}.",
                                buttons=[[Button.inline("◀️ Settings", b"asettings")]])
        except ValueError:
            await event.respond("❌ Send a valid number (e.g. `50`).")

    # ── ADMIN: ADD COUNTRY ────────────────────────────────────
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
                msg = f"♻️ **{name}** updated (₹{price_val:.0f})."
            else:
                await countries_col.insert_one({
                    "code":      code,
                    "name":      name,
                    "flag":      flag_,
                    "price":     price_val,
                    "is_active": True,
                })
                msg = f"✅ **{name}** added (₹{price_val:.0f})."
            user_states.pop(user_id, None)
            await event.respond(msg,
                                buttons=[[Button.inline("◀️ Countries", b"acountries")]])
        except Exception:
            await event.respond(
                "❌ Wrong format.  Use:\n`CODE | Name | Flag | Price`\n\n"
                "Example: `TR | Turkey | 🇹🇷 | 28`"
            )

    # ── ADMIN: USER SEARCH ────────────────────────────────────
    elif state == "search_user":
        try:
            tid    = int(text.strip())
            target = await users_col.find_one({"user_id": tid})
            if not target:
                await event.respond("❌ User not found.")
                return
            o_count = await orders_col.count_documents({"user_id": tid})
            d_count = await deposits_col.count_documents({"user_id": tid})
            banned_ = "🚫 Yes" if target.get("is_banned") else "✅ No"
            await event.respond(
                f"👤 **User Info**\n\n"
                f"ID:       `{tid}`\n"
                f"Balance:  `₹{target.get('balance', 0):.2f}`\n"
                f"Orders:   `{o_count}`\n"
                f"Deposits: `{d_count}`\n"
                f"Banned:   {banned_}\n"
                f"Joined:   {target.get('joined_at','?')}",
            )
            user_states.pop(user_id, None)
        except ValueError:
            await event.respond("❌ Send a valid Telegram user ID (numbers only).")

    # ── ADMIN: ADD BALANCE — step 1 (user ID) ────────────────
    elif state == "add_bal_uid":
        try:
            tid = int(text.strip())
            user_states[user_id] = {"state": "add_bal_amount", "target_id": tid}
            await event.respond(
                f"💰 How much balance to add for user `{tid}`?\n(₹ amount)"
            )
        except ValueError:
            await event.respond("❌ Invalid Telegram ID.")

    # ── ADMIN: ADD BALANCE — step 2 (amount) ─────────────────
    elif state == "add_bal_amount":
        try:
            amount  = float(text.strip())
            tid     = state_data["target_id"]
            await users_col.update_one({"user_id": tid}, {"$inc": {"balance": amount}})
            try:
                await bot.send_message(
                    tid,
                    f"💰 **₹{amount:.0f} added** to your wallet by admin!",
                )
            except Exception:
                pass
            user_states.pop(user_id, None)
            await event.respond(
                f"✅ ₹{amount:.0f} added to user `{tid}`.",
                buttons=[[Button.inline("◀️ Admin", b"admin")]],
            )
        except ValueError:
            await event.respond("❌ Invalid amount.")

    # ── ADMIN: BAN / UNBAN ────────────────────────────────────
    elif state == "ban_uid":
        try:
            tid    = int(text.strip())
            target = await users_col.find_one({"user_id": tid})
            if not target:
                await event.respond("❌ User not found.")
                user_states.pop(user_id, None)
                return
            new_ban = not target.get("is_banned", False)
            await users_col.update_one(
                {"user_id": tid}, {"$set": {"is_banned": new_ban}}
            )
            action = "banned" if new_ban else "unbanned"
            try:
                await bot.send_message(
                    tid,
                    "🚫 You have been **banned** from this bot."
                    if new_ban
                    else "✅ You have been **unbanned**. Welcome back!",
                )
            except Exception:
                pass
            user_states.pop(user_id, None)
            await event.respond(
                f"✅ User `{tid}` has been **{action}**.",
                buttons=[[Button.inline("◀️ Admin", b"admin")]],
            )
        except ValueError:
            await event.respond("❌ Invalid Telegram ID.")

    # ── ADMIN: ADD ADMIN ──────────────────────────────────────
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
                f"✅ **Admin Added:** {name_} (`{new_id}`)",
                buttons=[[Button.inline("◀️ Manage Admins", b"manage_admins")]],
            )
            try:
                await bot.send_message(
                    new_id, "🔑 You have been granted **admin access** to the bot!"
                )
            except Exception:
                pass
        except ValueError:
            await event.respond("❌ Invalid Telegram ID.")


# ══════════════════════════════════════════════════════════════
#  19. HEALTH-CHECK WEB SERVER  (FastAPI + Uvicorn)
#      Render needs an HTTP endpoint to confirm the service is
#      alive.  FastAPI runs in a background thread via Uvicorn
#      so it never blocks the bot's asyncio event loop.
# ══════════════════════════════════════════════════════════════
import threading
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

_web = FastAPI(title="Stark Bot", docs_url="/docs")


@_web.get("/", response_class=JSONResponse)
async def health():
    """Render / UptimeRobot health-check endpoint."""
    return {"status": "ok", "bot": "Stark Bot is running"}


@_web.get("/ping")
async def ping():
    return {"pong": True}


def _start_health_server() -> None:
    port = int(os.getenv("PORT", 8080))
    log.info(f"[health] FastAPI listening on port {port}")
    uvicorn.run(_web, host="0.0.0.0", port=port, log_level="warning")


# ══════════════════════════════════════════════════════════════
#  20. MAIN
# ══════════════════════════════════════════════════════════════
async def main():
    # Start health-check server in background thread (non-blocking)
    threading.Thread(target=_start_health_server, daemon=True).start()

    await init_db()
    await bot.start(bot_token=BOT_TOKEN)

    global acc_mgr
    acc_mgr = AccountManager(
        accounts_col, bot, API_ID, API_HASH, pending_otp_requests
    )
    await acc_mgr.load_all()

    bot_name = await get_setting("bot_name", "Stark Bot")
    log.info(f"🚀 {bot_name} is running…")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
