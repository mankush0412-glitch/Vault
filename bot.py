import os
import io
import re
import uuid
import asyncio
import contextlib
import zipfile
import hashlib
import logging
import random
import string
import tempfile
from datetime import datetime
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from telethon import TelegramClient
from telethon.sessions import StringSession

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes
)

import account_manager

# ─── ENV ──────────────────────────────────────
load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_NAME = os.getenv("DB_NAME", "stark_bot")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

# Force-join chat IDs
_fj_raw = os.getenv("FORCE_JOIN_CHAT_IDS", os.getenv("FORCE_JOIN_CHAT_ID", "")).strip()
RAW_CHAT_IDS: List[str] = [x.strip() for x in _fj_raw.split(",") if x.strip()]

if not all([API_ID, API_HASH, BOT_TOKEN, OWNER_ID]):
    raise ValueError("❌ .env incomplete!")

# ─── LOGGING ──────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# ─── MONGODB ──────────────────────────────────
_mongo = AsyncIOMotorClient(MONGO_URL)
db = _mongo[DB_NAME]
users_col = db["users"]
accounts_col = db["accounts"]
orders_col = db["orders"]
deposits_col = db["deposits"]
settings_col = db["settings"]
countries_col = db["countries"]
categories_col = db["categories"]
packages_col = db["packages"]
bot_admins_col = db["bot_admins"]

# ─── GLOBALS ──────────────────────────────────
acc_mgr: Optional[account_manager.AccountManager] = None
user_states: Dict[int, dict] = {}
pending_otp: Dict = {}
ADMIN_IDS = [OWNER_ID]
application: Optional[Application] = None

# ─── FANCY TEXT ──────────────────────────────
_SMALL = {
    'a':'ᴀ','b':'ʙ','c':'ᴄ','d':'ᴅ','e':'ᴇ',
    'f':'ғ','g':'ɢ','h':'ʜ','i':'ɪ','j':'ᴊ',
    'k':'ᴋ','l':'ʟ','m':'ᴍ','n':'ɴ','o':'ᴏ',
    'p':'ᴘ','q':'ǫ','r':'ʀ','s':'s','t':'ᴛ',
    'u':'ᴜ','v':'ᴠ','w':'ᴡ','x':'x','y':'ʏ','z':'ᴢ'
}
def fancy(text: str) -> str:
    return ''.join(_SMALL.get(c.lower(), c) for c in text)

# ─── DEFAULT DATA ────────────────────────────
DEFAULT_COUNTRIES = [
    {"code":"IN","name":"India","flag":"🇮🇳","price":30},
    {"code":"BD","name":"Bangladesh","flag":"🇧🇩","price":28},
    {"code":"PK","name":"Pakistan","flag":"🇵🇰","price":25},
    {"code":"NG","name":"Nigeria","flag":"🇳🇬","price":20},
    {"code":"ID","name":"Indonesia","flag":"🇮🇩","price":28},
    {"code":"US","name":"USA","flag":"🇺🇸","price":50},
    {"code":"VN","name":"Vietnam","flag":"🇻🇳","price":22},
    {"code":"MM","name":"Myanmar","flag":"🇲🇲","price":20},
    {"code":"KE","name":"Kenya","flag":"🇰🇪","price":22},
    {"code":"CO","name":"Colombia","flag":"🇨🇴","price":25},
    {"code":"ZW","name":"Zimbabwe","flag":"🇿🇼","price":18},
    {"code":"GB","name":"UK","flag":"🇬🇧","price":45},
    {"code":"RU","name":"Russia","flag":"🇷🇺","price":30},
    {"code":"BR","name":"Brazil","flag":"🇧🇷","price":25},
    {"code":"PH","name":"Philippines","flag":"🇵🇭","price":22},
    {"code":"EG","name":"Egypt","flag":"🇪🇬","price":20},
    {"code":"AU","name":"Australia","flag":"🇦🇺","price":55},
    {"code":"CA","name":"Canada","flag":"🇨🇦","price":50},
    {"code":"TR","name":"Turkey","flag":"🇹🇷","price":28},
    {"code":"DE","name":"Germany","flag":"🇩🇪","price":48},
    {"code":"FR","name":"France","flag":"🇫🇷","price":48},
    {"code":"IT","name":"Italy","flag":"🇮🇹","price":45},
    {"code":"ES","name":"Spain","flag":"🇪🇸","price":42},
    {"code":"MX","name":"Mexico","flag":"🇲🇽","price":35},
    {"code":"AR","name":"Argentina","flag":"🇦🇷","price":30},
    {"code":"TH","name":"Thailand","flag":"🇹🇭","price":22},
]

DEFAULT_CATEGORIES = [
    {"name":"Fresh Accounts","icon":"🆕","order":1},
    {"name":"Cheap Accounts","icon":"💰","order":2},
    {"name":"Old Accounts","icon":"📅","order":3},
    {"name":"Spam Accounts","icon":"📧","order":4},
    {"name":"Rare Accounts","icon":"⭐","order":5},
    {"name":"Number Change","icon":"🔄","order":6},
]

DEFAULT_PACKAGES = [
    {"credits":10,"price":10.0,"usdt":0.11,"stars":10},
    {"credits":25,"price":25.0,"usdt":0.27,"stars":25},
    {"credits":50,"price":50.0,"usdt":0.55,"stars":50},
    {"credits":100,"price":100.0,"usdt":1.10,"stars":100},
]

DEFAULT_SETTINGS = {
    "bot_name":"Next Level Vault","upi_id":"","upi_name":"Next Level Vault",
    "support_link":"","referral_bonus":10.0,"referral_percent":3.0,
    "min_deposit":10.0,"welcome_photo":"","whatsapp_enabled":False,
    "auto_verify_delay":10,"payment_webhook_url":"","default_2fa":"",
}

# ─── DATABASE INIT ────────────────────────────
async def init_db():
    await users_col.create_index("user_id", unique=True)
    await accounts_col.create_index([("country_code",1),("status",1)])
    await deposits_col.create_index("user_id")
    await orders_col.create_index("user_id")
    await settings_col.create_index("key", unique=True)
    await countries_col.create_index("code", unique=True)
    await bot_admins_col.create_index("telegram_id", unique=True)
    await categories_col.create_index("name", unique=True)
    await packages_col.create_index("credits", unique=True)

    for key,val in DEFAULT_SETTINGS.items():
        if not await settings_col.find_one({"key":key}):
            await settings_col.insert_one({"key":key,"value":val})

    for c in DEFAULT_COUNTRIES:
        if not await countries_col.find_one({"code":c["code"]}):
            await countries_col.insert_one({**c,"is_active":True})

    for cat in DEFAULT_CATEGORIES:
        if not await categories_col.find_one({"name":cat["name"]}):
            await categories_col.insert_one({**cat,"is_active":True})

    for pkg in DEFAULT_PACKAGES:
        if not await packages_col.find_one({"credits":pkg["credits"]}):
            await packages_col.insert_one(pkg)

    log.info("✅ Database initialised")

# ─── SETTINGS CACHE ────────────────────────────
_settings_cache = {}
_cache_ts = None
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
        {"key":key},
        {"$set":{"key":key,"value":value,"updated_at":datetime.utcnow()}},
        upsert=True
    )
    _settings_cache = {}
    _cache_ts = None

# ─── ADMIN HELPERS ─────────────────────────────
async def get_all_admin_ids() -> List[int]:
    ids = list(ADMIN_IDS)
    async for a in bot_admins_col.find({"is_active":True}):
        if a["telegram_id"] not in ids:
            ids.append(a["telegram_id"])
    return ids

async def is_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    doc = await bot_admins_col.find_one({"telegram_id":user_id,"is_active":True})
    return doc is not None

# ─── FORCE-JOIN ──────────────────────────────
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
        entity = await application.bot.get_chat(parsed)
        member = await application.bot.get_chat_member(entity.id, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

async def is_user_member(user_id: int) -> bool:
    if not RAW_CHAT_IDS:
        return True
    for raw in RAW_CHAT_IDS:
        if not await _is_member_of(raw, user_id):
            return False
    return True

async def send_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    buttons = []
    for raw in RAW_CHAT_IDS:
        if await _is_member_of(raw, user_id):
            continue
        title = raw
        try:
            parsed = _parse_chat_id(raw)
            if parsed:
                chat = await context.bot.get_chat(parsed)
                title = chat.title or raw
        except:
            pass
        if raw.startswith("@"):
            buttons.append([InlineKeyboardButton(f"📢 Join {title}", url=f"https://t.me/{raw[1:]}")])
        else:
            try:
                parsed = _parse_chat_id(raw)
                if parsed:
                    invite_link = await context.bot.create_chat_invite_link(parsed, member_limit=1)
                    buttons.append([InlineKeyboardButton(f"📢 Join {title}", url=invite_link.invite_link)])
            except:
                pass
    if not buttons:
        return
    buttons.append([InlineKeyboardButton("✅ Check Again", callback_data="check_join")])
    await update.message.reply_text(
        fancy("⚠️ **ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ(s) ʙᴇʟᴏᴡ ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ.**"),
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ─── USER HELPER ──────────────────────────────
async def get_or_create_user(user_id: int, referrer_id: Optional[int] = None) -> dict:
    user = await users_col.find_one({"user_id":user_id})
    if not user:
        ref_code = ''.join(random.choices(string.ascii_uppercase+string.digits,k=8))
        user = {
            "user_id":user_id,"balance":0.0,"withdrawable":0.0,
            "referral_code":ref_code,"referred_by":referrer_id,
            "referral_earnings":0.0,"is_banned":False,
            "joined_at":datetime.utcnow(),"language":"en","tier":"⭐"
        }
        await users_col.insert_one(user)
        if referrer_id and referrer_id != user_id:
            bonus = float(await get_setting("referral_bonus",10.0))
            if bonus > 0:
                await users_col.update_one(
                    {"user_id":referrer_id},
                    {"$inc":{"balance":bonus,"referral_earnings":bonus,"withdrawable":bonus}}
                )
                try:
                    await context.bot.send_message(
                        referrer_id,
                        fancy(f"🎁 **ʀᴇꜰᴇʀʀᴀʟ ʙᴏɴᴜs!**\n+₹{bonus:.0f} ᴄʀᴇᴅɪᴛᴇᴅ.")
                    )
                except:
                    pass
    return user

# ─── COUNTRIES ──────────────────────────────
async def get_active_countries(category: str = None) -> List[dict]:
    result = []
    async for c in countries_col.find({"is_active":True}):
        stock_query = {"country_code":c["code"],"status":"available"}
        if category:
            stock_query["category"] = category
        stock = await accounts_col.count_documents(stock_query)
        result.append({
            "code":c["code"],"name":c["name"],"flag":c["flag"],
            "price":c["price"],"stock":stock
        })
    return result

async def get_categories() -> List[dict]:
    cats = []
    async for cat in categories_col.find({"is_active":True}).sort("order",1):
        cats.append(cat)
    return cats

# ─── SESSION HELPERS ──────────────────────────
def _phone_from_filename(name: str) -> str:
    base = os.path.splitext(os.path.basename(name))[0]
    digits = re.sub(r"[^\d]","",base)
    return f"+{digits}" if len(digits) >= 7 else base

def _detect_session_format(session_bytes: bytes):
    import sqlite3, struct, ipaddress as _ip
    _DC_IP = {1:"149.154.175.53",2:"149.154.167.51",3:"149.154.175.100",
              4:"149.154.167.91",5:"91.108.56.130"}
    tmp = tempfile.NamedTemporaryFile(suffix=".session", delete=False)
    try:
        tmp.write(session_bytes); tmp.flush(); tmp.close()
        conn = sqlite3.connect(tmp.name)
        cur = conn.cursor()
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
                dc_id = row[0]; server = _DC_IP.get(dc_id, _DC_IP[2])
                return "pyrogram", dc_id, server, 443, row[1]
        else:
            conn.close()
    except Exception as e:
        log.warning(f"[detect_fmt] sqlite error: {e}")
    finally:
        with contextlib.suppress(Exception):
            os.unlink(tmp.name)
    return None, None, None, None, None

def _pyrogram_to_telethon_ss(dc_id: int, server: str, port: int, auth_key: bytes) -> Optional[str]:
    import struct, base64, ipaddress as _ip
    try:
        if not isinstance(auth_key, bytes) or len(auth_key) != 256:
            return None
        ip_bytes = _ip.ip_address(server).packed
        payload = struct.pack(">B", dc_id) + ip_bytes + struct.pack(">H", port) + auth_key
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

# ─── UPI QR ────────────────────────────────────
def _make_upi_qr(upi_id: str, amount: float, name: str) -> Optional[bytes]:
    try:
        import qrcode as qrc
        uri = f"upi://pay?pa={upi_id}&pn={name}&am={amount:.2f}&cu=INR&tn=NextLevelVaultDeposit"
        buf = io.BytesIO()
        qrc.make(uri).save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        log.warning(f"[QR] Failed to generate UPI QR: {e}")
        return None

# ─── KEYBOARD BUILDERS ──────────────────────
def get_main_menu(user_id: int) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(fancy("✈️ ʙᴜʏ ᴛᴇʟᴇɢʀᴀᴍ ᴀᴄᴄᴏᴜɴᴛ"), callback_data="store")],
    ]
    # WhatsApp if enabled
    if asyncio.run_coroutine_threadsafe(get_setting("whatsapp_enabled", False), loop).result():
        kb.append([InlineKeyboardButton(fancy("💬 ʙᴜʏ ᴡʜᴀᴛsᴀᴘᴘ"), callback_data="whatsapp")])
    kb.append([InlineKeyboardButton(fancy("💳 ᴀᴅᴅ ᴄʀᴇᴅɪᴛs"), callback_data="deposit")])
    kb.append([
        InlineKeyboardButton(fancy("👤 ᴍʏ ᴘʀᴏꜰɪʟᴇ"), callback_data="profile"),
        InlineKeyboardButton(fancy("🌐 ʟᴀɴɢᴜᴀɢᴇ"), callback_data="language")
    ])
    kb.append([InlineKeyboardButton(fancy("❓ ʜᴇʟᴘ"), callback_data="help")])
    if asyncio.run_coroutine_threadsafe(is_admin(user_id), loop).result():
        kb.append([InlineKeyboardButton(fancy("⚙️ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ"), callback_data="admin")])
    return InlineKeyboardMarkup(kb)

def get_category_buttons(categories: List[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(categories), 2):
        row = []
        for cat in categories[i:i+2]:
            row.append(InlineKeyboardButton(
                fancy(f"{cat['icon']} {cat['name']}"),
                callback_data=f"category:{cat['name']}"
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

def get_inventory_buttons(countries: List[dict], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    total = len(countries)
    start = page * per_page
    end = min(start + per_page, total)
    rows = []
    for i in range(start, end, 2):
        row = []
        for c in countries[i:i+2]:
            stock = c["stock"]
            price = c["price"]
            label = f"{c['flag']} {fancy(c['name'])} | {stock} | {price} ᴄʀ"
            if stock == 0:
                label += " ❌"
                callback = "noop"
            else:
                callback = f"buy:{c['code']}"
            row.append(InlineKeyboardButton(label, callback_data=callback))
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"store_page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{(total-1)//per_page + 1}", callback_data="noop"))
    if end < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"store_page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

def get_admin_menu(is_owner: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(fancy("📊 sᴛᴀᴛs"), callback_data="astats")],
        [InlineKeyboardButton(fancy("📦 ᴜᴘʟᴏᴀᴅ sᴇssɪᴏɴs"), callback_data="upload_sessions"),
         InlineKeyboardButton(fancy("📋 sᴇssɪᴏɴ ᴏᴠᴇʀᴠɪᴇᴡ"), callback_data="manage_sessions")],
        [InlineKeyboardButton(fancy("💳 ᴘᴇɴᴅɪɴɢ ᴅᴇᴘᴏsɪᴛs"), callback_data="pending_deposits")],
        [InlineKeyboardButton(fancy("📢 ʙʀᴏᴀᴅᴄᴀsᴛ"), callback_data="broadcast")],
        [InlineKeyboardButton(fancy("⚙️ sᴇᴛᴛɪɴɢs"), callback_data="asettings"),
         InlineKeyboardButton(fancy("🌍 ᴄᴏᴜɴᴛʀɪᴇs"), callback_data="acountries")],
        [InlineKeyboardButton(fancy("👤 ᴜsᴇʀs"), callback_data="ausers")],
    ]
    if is_owner:
        rows.append([InlineKeyboardButton(fancy("🔑 ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs"), callback_data="manage_admins")])
    rows.append([InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

# ─── BOT HANDLERS ──────────────────────────────

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    referrer_id = None
    if args:
        val = args[0].lstrip("ref").lstrip("_")
        try:
            referrer_id = int(val)
        except ValueError:
            ref_user = await users_col.find_one({"referral_code": val})
            if ref_user:
                referrer_id = ref_user["user_id"]

    user = await get_or_create_user(user_id, referrer_id)
    if user.get("is_banned"):
        await update.message.reply_text("🚫 You are banned from this bot.")
        return

    # Force-join check
    if not await is_user_member(user_id):
        await send_join_message(update, context)
        return

    first_name = update.effective_user.first_name or "User"
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
            await update.message.reply_photo(
                photo=photo_id,
                caption=welcome_msg,
                reply_markup=get_main_menu(user_id)
            )
            return
        except:
            pass
    await update.message.reply_text(welcome_msg, reply_markup=get_main_menu(user_id))

# Callback query handler (main router)
async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    user = await users_col.find_one({"user_id": user_id})
    if user and user.get("is_banned"):
        await query.edit_message_text("🚫 You are banned.")
        return

    # Force-join check for all callbacks except check_join
    if data != "check_join" and not await is_user_member(user_id):
        await query.edit_message_text(
            fancy("⚠️ **ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ(s) ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ.**"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Check Again", callback_data="check_join")]
            ])
        )
        return

    # ── CHECK JOIN ────────────────────────────
    if data == "check_join":
        if await is_user_member(user_id):
            await query.edit_message_text("✅ Verified! Restart bot with /start")
            await start(update, context)
        else:
            await query.answer("❌ You haven't joined yet!", show_alert=True)
        return

    # ── MAIN MENU ─────────────────────────────
    if data == "main_menu":
        user_states.pop(user_id, None)
        bot_name = await get_setting("bot_name", "Next Level Vault")
        await query.edit_message_text(
            fancy(f"🏠 {bot_name}\n\nᴄʜᴏᴏsᴇ ᴀɴ ᴏᴘᴛɪᴏɴ:"),
            reply_markup=get_main_menu(user_id)
        )

    # ── STORE ──────────────────────────────────
    elif data == "store":
        cats = await get_categories()
        await query.edit_message_text(
            fancy("📂 **sᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ**\n\n👆 ᴛᴀᴘ ᴀ ᴄᴀᴛᴇɢᴏʀʏ ʙᴇʟᴏᴡ:"),
            reply_markup=get_category_buttons(cats)
        )

    elif data.startswith("category:"):
        cat_name = data.split(":",1)[1]
        user_states[user_id] = {"category": cat_name}
        countries = await get_active_countries(cat_name)
        if not countries:
            await query.edit_message_text(
                fancy(f"😔 **ɴᴏ ᴀᴄᴄᴏᴜɴᴛs ɪɴ '{cat_name}'.**\n\nᴛʀʏ ᴀɴᴏᴛʜᴇʀ ᴄᴀᴛᴇɢᴏʀʏ."),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="store")]
                ])
            )
            return
        await query.edit_message_text(
            fancy(f"📌 **{cat_name}**\n\nᴄᴏᴜɴᴛʀʏ → sᴛᴏᴄᴋ → ᴘʀɪᴄᴇ → ʙᴜʏ"),
            reply_markup=get_inventory_buttons(countries, 0)
        )

    elif data.startswith("store_page:"):
        page = int(data.split(":")[1])
        cat_name = user_states.get(user_id, {}).get("category")
        countries = await get_active_countries(cat_name)
        await query.edit_message_text(
            fancy(f"📌 **{cat_name or 'ᴀʟʟ'}**\n\nᴄᴏᴜɴᴛʀʏ → sᴛᴏᴄᴋ → ᴘʀɪᴄᴇ → ʙᴜʏ"),
            reply_markup=get_inventory_buttons(countries, page)
        )

    elif data == "noop":
        await query.answer()

    # ── BUY ──────────────────────────────────────
    elif data.startswith("buy:"):
        code = data.split(":")[1]
        country = await countries_col.find_one({"code":code,"is_active":True})
        if not country:
            await query.answer("❌ Country unavailable.", show_alert=True)
            return
        cat_name = user_states.get(user_id, {}).get("category")
        stock = await accounts_col.count_documents({
            "country_code":code,"status":"available","category":cat_name
        })
        if stock == 0:
            await query.answer("❌ Out of stock!", show_alert=True)
            return
        user = await get_or_create_user(user_id)
        bal = float(user.get("balance",0))
        price = float(country["price"])
        if bal < price:
            needed = price - bal
            text = fancy(
                f"⚠️ **ɪɴsᴜꜰꜰɪᴄɪᴇɴᴛ ꜰᴜɴᴅs**\n\n"
                f"ᴀᴄᴄᴏᴜɴᴛ: {country['flag']} {country['name']} (+••••••••)\n"
                f"ᴘʀɪᴄᴇ: `{price} ᴄʀ`\n"
                f"ᴀᴠᴀɪʟᴀʙʟᴇ ꜰᴜɴᴅs: `{bal} ᴄʀ`\n"
                f"sʜᴏʀᴛ ʙʏ: `{needed} ᴄʀ`\n\n"
                "ᴘʟᴇᴀsᴇ ᴀᴅᴅ ꜰᴜɴᴅs ᴛᴏ ᴘʀᴏᴄᴇᴇᴅ."
            )
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("+1 ᴀᴅᴅ ꜰᴜɴᴅs"), callback_data="deposit")],
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="store")],
                [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
            ]))
            return
        # Confirm
        text = fancy(
            f"⚡ **ᴄᴏɴꜰɪʀᴍ ᴘᴜʀᴄʜᴀsᴇ**\n\n"
            f"🌍 ᴄᴏᴜɴᴛʀʏ: {country['flag']} {country['name']}\n"
            f"💰 ᴘʀɪᴄᴇ: `{price} ᴄʀ`\n"
            f"💎 ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ: `{bal} ᴄʀ`"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy(f"✅ ʙᴜʏ ᴛʜɪs ᴀᴄᴄᴏᴜɴᴛ · {price} ᴄʀ"), callback_data=f"confirm_buy:{code}")],
            [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="store")],
            [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
        ]))

    elif data.startswith("confirm_buy:"):
        code = data.split(":")[1]
        country = await countries_col.find_one({"code":code,"is_active":True})
        if not country:
            await query.answer("❌ Country no longer available.", show_alert=True)
            return
        price = float(country["price"])
        user = await get_or_create_user(user_id)
        bal = float(user.get("balance",0))
        if bal < price:
            await query.answer("❌ Insufficient balance!", show_alert=True)
            return
        cat_name = user_states.get(user_id, {}).get("category")
        session_doc = await accounts_col.find_one_and_update(
            {"country_code":code,"status":"available","category":cat_name},
            {"$set":{"status":"sold","buyer_id":user_id,"sold_at":datetime.utcnow()}}
        )
        if not session_doc:
            await query.answer("❌ Out of stock — someone just bought the last one!", show_alert=True)
            return
        await users_col.update_one({"user_id":user_id}, {"$inc":{"balance":-price}})
        if user.get("referred_by"):
            pct = float(await get_setting("referral_percent",3.0))
            bonus = round(price * pct / 100, 2)
            if bonus > 0:
                await users_col.update_one(
                    {"user_id":user["referred_by"]},
                    {"$inc":{"balance":bonus,"referral_earnings":bonus,"withdrawable":bonus}}
                )
                try:
                    await context.bot.send_message(
                        user["referred_by"],
                        fancy(f"💸 **ʀᴇꜰᴇʀʀᴀʟ ᴇᴀʀɴɪɴɢ!**\n+₹{bonus:.2f} ғʀᴏᴍ ᴘᴜʀᴄʜᴀsᴇ.")
                    )
                except:
                    pass
        phone = session_doc["phone"]
        twofa = session_doc.get("twofa_password","")
        await orders_col.insert_one({
            "user_id":user_id,"phone":phone,"country":country["name"],
            "country_code":code,"country_flag":country.get("flag",""),
            "amount":price,"twofa":twofa,"category":cat_name,
            "status":"waiting_otp","created_at":datetime.utcnow()
        })
        pending_otp[(user_id, phone)] = True
        msg = fancy(
            f"✅ **ᴘᴜʀᴄʜᴀsᴇ sᴜᴄᴄᴇssꜰᴜʟ!**\n\n"
            f"🌍 ᴄᴏᴜɴᴛʀʏ: {country.get('flag','')} {country['name']}\n"
            f"📞 ᴘʜᴏɴᴇ: `{phone}`\n"
            f"📂 ᴄᴀᴛᴇɢᴏʀʏ: `{cat_name}`\n"
            f"🔐 2FA: `{twofa}`\n\n"
            "⏳ ᴘʟᴇᴀsᴇ ᴛᴀᴘ **ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ** ᴛᴏ ɢᴇᴛ ʏᴏᴜʀ ᴄᴏᴅᴇ."
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy("📩 ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ"), callback_data=f"resend_{phone}")],
            [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
        ]))

    elif data.startswith("resend_"):
        phone = data[7:]
        await query.answer("⏳ Requesting OTP…")
        success = await acc_mgr.request_otp(phone)
        if success:
            await context.bot.send_message(user_id, f"📤 OTP request sent for `{phone}`.")
        else:
            await context.bot.send_message(user_id, f"❌ Could not trigger OTP for `{phone}`.")

    elif data.startswith("logout_"):
        phone = data[7:]
        await acc_mgr.logout_client(phone)
        await accounts_col.find_one_and_update(
            {"phone":phone,"status":"sold"},
            {"$set":{"status":"logged_out","logged_out_at":datetime.utcnow()}},
            sort=[("sold_at",-1)]
        )
        await query.edit_message_text(
            fancy(f"🔓 **ʟᴏɢɢᴇᴅ ᴏᴜᴛ**\n\n`{phone}` ʜᴀs ʙᴇᴇɴ ᴅɪsᴄᴏɴɴᴇᴄᴛᴇᴅ."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
            ])
        )

    # ── MY ORDERS ──────────────────────────────
    elif data == "orders":
        docs = await orders_col.find({"user_id":user_id}).sort("created_at",-1).limit(8).to_list(8)
        if not docs:
            await query.edit_message_text(
                fancy("📋 **ᴍʏ ᴏʀᴅᴇʀs**\n\n_ɴᴏ ᴏʀᴅᴇʀs ʏᴇᴛ._"),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("🛒 ʙᴜʏ ɴᴏᴡ"), callback_data="store"),
                     InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
                ])
            )
            return
        _STATUS = {"waiting_otp":"⏳ ᴡᴀɪᴛɪɴɢ ᴏᴛᴘ","completed":"✅ ᴄᴏᴍᴘʟᴇᴛᴇᴅ",
                   "cancelled":"❌ ᴄᴀɴᴄᴇʟʟᴇᴅ","logged_out":"🔓 ʟᴏɢɢᴇᴅ ᴏᴜᴛ"}
        lines = [fancy("📋 **ᴍʏ ᴏʀᴅᴇʀs — ʟᴀsᴛ 8**\n")]
        for i,o in enumerate(docs,1):
            st = _STATUS.get(o.get("status",""), o.get("status","").title())
            line = f"**{i}.** {o.get('country_flag','')} **{o.get('country','?')}**  —  {st}\n📱 `{o.get('phone','?')}`  •  {o.get('amount',0):.0f} ᴄʀ"
            if o.get("twofa"):
                line += f"\n🔐 2FA: `{o['twofa']}`"
            lines.append(line)
        kb = [[InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]]
        if docs[0].get("status") == "waiting_otp":
            kb.insert(0, [InlineKeyboardButton(fancy("📩 ʀᴇ-ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ"), callback_data=f"resend_{docs[0]['phone']}")])
        await query.edit_message_text("\n\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

    # ── HISTORY ──────────────────────────────────
    elif data == "history":
        orders = await orders_col.find({"user_id":user_id}).sort("created_at",-1).limit(6).to_list(6)
        deps = await deposits_col.find({"user_id":user_id}).sort("created_at",-1).limit(5).to_list(5)
        spent = sum(float(o.get("amount",0)) for o in orders if o.get("status")!="cancelled")
        dep_tot = sum(float(d.get("amount",0)) for d in deps if d.get("status")=="approved")
        lines = [fancy("📋 **ʜɪsᴛᴏʀʏ**\n"),
                 f"💸 ᴛᴏᴛᴀʟ sᴘᴇɴᴛ:     `{spent:.2f} ᴄʀ`",
                 f"💰 ᴛᴏᴛᴀʟ ᴅᴇᴘᴏsɪᴛᴇᴅ: `{dep_tot:.2f} ᴄʀ`"]
        if orders:
            lines.append("\n🛒 **ʀᴇᴄᴇɴᴛ ᴘᴜʀᴄʜᴀsᴇs:**")
            for o in orders:
                lines.append(f"• {o.get('country_flag','')} {o.get('country','?')} — `{o.get('amount',0):.0f} ᴄʀ` — `{o.get('phone','?')}`")
        if deps:
            lines.append("\n💰 **ʀᴇᴄᴇɴᴛ ᴅᴇᴘᴏsɪᴛs:**")
            _DE = {"approved":"✅","pending":"⏳","rejected":"❌"}
            for d in deps:
                lines.append(f"{_DE.get(d.get('status',''),'•')} {d.get('amount',0):.0f} ᴄʀ — {d.get('status','').title()}")
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
        ]))

    # ── DEPOSIT ──────────────────────────────────
    elif data == "deposit":
        pkgs = await packages_col.find({}).sort("credits",1).to_list(None)
        rows = []
        for i in range(0, len(pkgs), 2):
            row = []
            for p in pkgs[i:i+2]:
                row.append(InlineKeyboardButton(
                    fancy(f"💎 {p['credits']} ᴄʀᴇᴅɪᴛs · ₹{p['price']}"),
                    callback_data=f"pkg:{p['credits']}"
                ))
            rows.append(row)
        rows.append([InlineKeyboardButton(fancy("✏️ ᴄᴜsᴛᴏᴍ ᴀᴍᴏᴜɴᴛ"), callback_data="custom_deposit")])
        rows.append([InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")])
        await query.edit_message_text(
            fancy("💳 **ᴀᴅᴅ ᴄʀᴇᴅɪᴛs**\n\n• ᴘᴜʀᴄʜᴀsᴇ ᴄʀᴇᴅɪᴛs: `0 ᴄʀ`\n• ᴛᴏᴛᴀʟ ᴘᴜʀᴄʜᴀsɪɴɢ ᴘᴏᴡᴇʀ: `0 ᴄʀ`\n\n⚡ ɪɴsᴛᴀɴᴛ & ᴀᴜᴛᴏᴍᴀᴛᴇᴅ\n💡 ʀᴀᴛᴇ: `1 ᴄʀ = ₹1 / $0.01`\n\nsᴇʟᴇᴄᴛ ᴀ ᴘᴀᴄᴋᴀɢᴇ ʙᴇʟᴏᴡ:"),
            reply_markup=InlineKeyboardMarkup(rows)
        )

    elif data.startswith("pkg:"):
        credits = int(data.split(":")[1])
        pkg = await packages_col.find_one({"credits":credits})
        if not pkg:
            await query.answer("❌ Package not found.", show_alert=True)
            return
        user_states[user_id] = {"deposit_pkg":credits,"pkg_data":pkg}
        methods = [
            [InlineKeyboardButton(fancy(f"🌐 ᴜᴘɪ / Qʀ – ₹{pkg['price']}"), callback_data=f"pay_method:upi:{credits}")],
            [InlineKeyboardButton(fancy(f"₿ ᴜsᴅᴛ (Cʀʏᴘᴛᴏ) – {pkg['usdt']} USDT"), callback_data=f"pay_method:usdt:{credits}")],
            [InlineKeyboardButton(fancy(f"⭐ ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs – {pkg['stars']} ⭐"), callback_data=f"pay_method:stars:{credits}")],
            [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ ᴛᴏ ᴘᴀᴄᴋᴀɢᴇs"), callback_data="deposit")]
        ]
        await query.edit_message_text(
            fancy(f"💳 **sᴇʟᴇᴄᴛ ᴘᴀʏᴍᴇɴᴛ ᴍᴇᴛʜᴏᴅ**\n\nᴘᴀᴄᴋᴀɢᴇ: `{credits} ᴄʀᴇᴅɪᴛs`\n₹{pkg['price']} | {pkg['usdt']} USDT | {pkg['stars']} ⭐"),
            reply_markup=InlineKeyboardMarkup(methods)
        )

    elif data.startswith("pay_method:"):
        _, method, credits_str = data.split(":")
        credits = int(credits_str)
        pkg = await packages_col.find_one({"credits":credits})
        if not pkg:
            await query.answer("❌ Package error.", show_alert=True)
            return
        amount = pkg["price"]

        if method == "upi":
            upi_id = await get_setting("upi_id")
            upi_name = await get_setting("upi_name","Next Level Vault")
            if not upi_id:
                await query.answer("❌ UPI not configured.", show_alert=True)
                return
            deposit_id = str(uuid.uuid4())[:8]
            await deposits_col.insert_one({
                "deposit_id":deposit_id,"user_id":user_id,"amount":amount,
                "credits":credits,"method":"upi","status":"pending",
                "created_at":datetime.utcnow()
            })
            user_states[user_id] = {"deposit_id":deposit_id,"credits":credits,"amount":amount}
            qr = _make_upi_qr(upi_id, amount, upi_name)
            msg = fancy(
                f"⚡ **sᴄᴀɴ & ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ**\n\n"
                f"• ᴀᴍᴏᴜɴᴛ: `₹{amount:.2f}`\n"
                f"• ꜰᴜɴᴅs: `+{credits}`\n"
                f"• ᴜᴘɪ ɪᴅ: `{upi_id}`\n\n"
                "sᴄᴀɴ Qʀ ᴏʀ ᴜsᴇ 'ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ' ʙᴜᴛᴛᴏɴ.\n"
                "ᴀғᴛᴇʀ ᴘᴀʏɪɴɢ, ᴛᴀᴘ **'ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ'**."
            )
            buttons = [
                [InlineKeyboardButton(fancy("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ"), callback_data="deposit_paid")],
                [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="cancel_payment")],
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="deposit")]
            ]
            if qr:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=io.BytesIO(qr),
                    caption=msg,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                await query.delete_message()
            else:
                await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons))

        elif method == "usdt":
            await query.edit_message_text(
                fancy(f"₿ **sᴇʟᴇᴄᴛ Nᴇᴛᴡᴏʀᴋ ғᴏʀ USDT Dᴇᴘᴏsɪᴛ**\n\nᴄʜᴏᴏsᴇ ᴛʜᴇ ʙʟᴏᴄᴋᴄʜᴀɪɴ ɴᴇᴛᴡᴏʀᴋ ʏᴏᴜ ᴡɪsʜ ᴛᴏ sᴇɴᴅ USDT ᴏɴ:"),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("⭐ BSC (BEP20)"), callback_data=f"usdt_net:bsc:{credits}")],
                    [InlineKeyboardButton(fancy("⭐ TRC20 (TRON)"), callback_data=f"usdt_net:trc20:{credits}")],
                    [InlineKeyboardButton(fancy("⭐ ERC20 (Ethereum)"), callback_data=f"usdt_net:erc20:{credits}")],
                    [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ ᴛᴏ ᴘᴀʏᴍᴇɴᴛ ᴍᴇᴛʜᴏᴅs"), callback_data="deposit")]
                ])
            )

        elif method == "stars":
            stars = pkg["stars"]
            msg = fancy(
                f"⭐ **ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs**\n\n"
                f"ᴘᴀʏ `{stars} ⭐` ᴛᴏ ɢᴇᴛ `{credits} ᴄʀᴇᴅɪᴛs`.\n"
                "ᴛʜɪs ᴡɪʟʟ ʀᴇᴅɪʀᴇᴄᴛ ᴛᴏ ᴛᴇʟᴇɢʀᴀᴍ's ᴏғғɪᴄɪᴀʟ ᴘᴀʏᴍᴇɴᴛ."
            )
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("⭐ ᴘᴀʏ sᴛᴀʀs"), callback_data=f"pay_stars:{credits}")],
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="deposit")]
            ]))

    elif data.startswith("usdt_net:"):
        _, network, credits_str = data.split(":")
        credits = int(credits_str)
        pkg = await packages_col.find_one({"credits":credits})
        if not pkg:
            await query.answer("❌ Package error.", show_alert=True)
            return
        usdt_amount = pkg["usdt"]
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
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy("📋 ᴄᴏᴘʏ ᴅᴇᴘᴏsɪᴛ ᴀᴅᴅʀᴇss"), callback_data=f"copy_addr:{addr}")],
            [InlineKeyboardButton(fancy("📋 ᴄᴏᴘʏ ᴀᴍᴏᴜɴᴛ"), callback_data=f"copy_amount:{usdt_amount}")],
            [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="cancel_payment")],
            [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="deposit")]
        ]))

    elif data.startswith("copy_addr:"):
        addr = data.split(":",1)[1]
        await query.answer("✅ Address copied to clipboard!", show_alert=True)
        await context.bot.send_message(user_id, f"`{addr}`")

    elif data.startswith("copy_amount:"):
        amt = data.split(":",1)[1]
        await query.answer("✅ Amount copied to clipboard!", show_alert=True)
        await context.bot.send_message(user_id, f"`{amt}`")

    elif data == "cancel_payment":
        dep = await deposits_col.find_one({"user_id":user_id,"status":"pending"}, sort=[("_id",-1)])
        if dep:
            await deposits_col.update_one({"dep_id":dep["dep_id"]}, {"$set":{"status":"cancelled"}})
        await query.edit_message_text(
            fancy("❌ **ᴘᴀʏᴍᴇɴᴛ ᴄᴀɴᴄᴇʟʟᴇᴅ.**\n\nɴᴏ ᴄʜᴀʀɢᴇs ᴡᴇʀᴇ ᴍᴀᴅᴇ."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="deposit")]
            ])
        )

    elif data == "deposit_paid":
        state = user_states.get(user_id, {})
        deposit_id = state.get("deposit_id")
        if not deposit_id:
            await query.answer("❌ No active deposit.", show_alert=True)
            return
        await query.answer("⏳ Verifying payment…")
        delay = int(await get_setting("auto_verify_delay", 10))
        asyncio.create_task(auto_verify_payment(user_id, deposit_id, delay))
        await query.edit_message_text(
            fancy("⏳ **ᴘᴀʏᴍᴇɴᴛ ᴠᴇʀɪғʏɪɴɢ…**\n\nᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ᴀ ғᴇᴡ sᴇᴄᴏɴᴅs.\nʏᴏᴜ ᴄᴀɴ ᴛᴀᴘ 'ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ' ᴀɴʏᴛɪᴍᴇ."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ"), callback_data="check_payment")]
            ])
        )

    elif data == "check_payment":
        state = user_states.get(user_id, {})
        deposit_id = state.get("deposit_id")
        if not deposit_id:
            await query.answer("❌ No active deposit.", show_alert=True)
            return
        dep = await deposits_col.find_one({"deposit_id":deposit_id})
        if not dep:
            await query.answer("❌ Deposit not found.", show_alert=True)
            return
        status = dep.get("status")
        if status == "approved":
            await query.edit_message_text(
                fancy(f"✅ **ᴘᴀʏᴍᴇɴᴛ ᴄᴏɴғɪʀᴍᴇᴅ!**\n\n`{dep.get('credits',0)} ᴄʀᴇᴅɪᴛs` ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ."),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
                ])
            )
            user_states.pop(user_id, None)
        elif status == "pending":
            await query.answer("⏳ Still verifying… Please wait.", show_alert=True)
            await query.edit_message_text(
                fancy("⚠️ **ᴘᴀʏᴍᴇɴᴛ ɴᴏᴛ ᴅᴇᴛᴇᴄᴛᴇᴅ ʏᴇᴛ.**\n\nᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ᴀ ғᴇᴡ sᴇᴄᴏɴᴅs ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ."),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ"), callback_data="check_payment")],
                    [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="cancel_payment")]
                ])
            )
        else:
            await query.answer(f"Status: {status}", show_alert=True)

    elif data.startswith("pay_stars:"):
        credits = int(data.split(":")[1])
        await query.answer("⭐ Stars payment is not fully implemented yet.", show_alert=True)

    elif data == "custom_deposit":
        user_states[user_id] = {"state":"custom_deposit"}
        min_dep = await get_setting("min_deposit",10.0)
        await query.edit_message_text(
            fancy(f"💡 **ᴄᴜsᴛᴏᴍ ᴀᴍᴏᴜɴᴛ**\n\nᴇɴᴛᴇʀ ᴛʜᴇ ᴀᴍᴏᴜɴᴛ ɪɴ ₹ (ᴍɪɴɪᴍᴜᴍ {min_dep}):"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="deposit")]
            ])
        )

    # ── PROFILE ──────────────────────────────────
    elif data == "profile":
        user = await get_or_create_user(user_id)
        bal = float(user.get("balance",0))
        wd = float(user.get("withdrawable",0))
        orders_count = await orders_col.count_documents({"user_id":user_id})
        deps_count = await deposits_col.count_documents({"user_id":user_id})
        tier = user.get("tier","⭐")
        lang = user.get("language","en")
        await query.edit_message_text(
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
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("📋 ᴏʀᴅᴇʀs"), callback_data="orders"),
                 InlineKeyboardButton(fancy("📜 ʜɪsᴛᴏʀʏ"), callback_data="history")],
                [InlineKeyboardButton(fancy("🎁 ʀᴇꜰᴇʀʀᴀʟ"), callback_data="referral"),
                 InlineKeyboardButton(fancy("🌐 ʟᴀɴɢᴜᴀɢᴇ"), callback_data="language")],
                [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
            ])
        )

    # ── LANGUAGE ──────────────────────────────────
    elif data == "language":
        langs = [
            ("🇬🇧 English","en"),("🇮🇳 हिन्दी","hi"),("🇷🇺 Русский","ru"),
            ("🇹🇷 Türkçe","tr"),("🇮🇳 தமிழ்","ta"),("🇮🇳 മലയാളം","ml")
        ]
        rows = []
        for label, code in langs:
            rows.append([InlineKeyboardButton(label, callback_data=f"set_lang:{code}")])
        rows.append([InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")])
        await query.edit_message_text(
            fancy("🌐 **ʟᴀɴɢᴜᴀɢᴇ sᴇᴛᴛɪɴɢs**\n\nᴄʜᴏᴏsᴇ ʏᴏᴜʀ ᴘʀᴇғᴇʀʀᴇᴅ ʟᴀɴɢᴜᴀɢᴇ:"),
            reply_markup=InlineKeyboardMarkup(rows)
        )

    elif data.startswith("set_lang:"):
        lang_code = data.split(":")[1]
        await users_col.update_one({"user_id":user_id}, {"$set":{"language":lang_code}})
        await query.answer(f"✅ Language set to {lang_code}", show_alert=True)
        # refresh profile
        await query.edit_message_text(
            fancy("👤 **ᴘʀᴏꜰɪʟᴇ**\n\nʟᴀɴɢᴜᴀɢᴇ ᴜᴘᴅᴀᴛᴇᴅ."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="profile")]
            ])
        )

    # ── REFERRAL ──────────────────────────────────
    elif data == "referral":
        user = await get_or_create_user(user_id)
        pct = float(await get_setting("referral_percent",3.0))
        bonus = float(await get_setting("referral_bonus",10.0))
        ref_code = user.get("referral_code","")
        earnings = float(user.get("referral_earnings",0))
        count = await users_col.count_documents({"referred_by":user_id})
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start=ref_{ref_code}"
        await query.edit_message_text(
            fancy(
                f"🎁 **ʀᴇꜰᴇʀ & ᴇᴀʀɴ**\n\n"
                f"ᴇᴀʀɴ **{pct:.1f}%** ᴏɴ ᴇᴠᴇʀʏ ᴅᴇᴘᴏsɪᴛ!\n"
                f"ᴘʟᴜs **₹{bonus:.0f}** ɪɴsᴛᴀɴᴛ ᴊᴏɪɴ ʙᴏɴᴜs!\n\n"
                f"🔗 **ʏᴏᴜʀ ʟɪɴᴋ:**\n`{ref_link}`\n\n"
                f"👥 ᴛᴏᴛᴀʟ ʀᴇꜰᴇʀʀᴇᴅ: **{count}**\n"
                f"💰 ᴛᴏᴛᴀʟ ᴇᴀʀɴᴇᴅ: `₹{earnings:.2f}`"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 sʜᴀʀᴇ ʟɪɴᴋ", url=f"https://t.me/share/url?url={ref_link}&text=Buy+Telegram+accounts+instantly!")],
                [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
            ])
        )

    # ── HELP ──────────────────────────────────────
    elif data == "help":
        await query.edit_message_text(
            fancy("❓ **ʜᴇʟᴘ ᴄᴇɴᴛᴇʀ**\n\nᴄʜᴏᴏsᴇ ᴀ ᴛᴏᴘɪᴄ ʙᴇʟᴏᴡ:"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("📖 ʜᴏᴡ ᴛᴏ ᴜsᴇ"), callback_data="help_howto")],
                [InlineKeyboardButton(fancy("🔑 ᴏᴛᴘ ʜᴇʟᴘ"), callback_data="help_otp")],
                [InlineKeyboardButton(fancy("💳 ᴘᴀʏᴍᴇɴᴛ ʜᴇʟᴘ"), callback_data="help_payment")],
                [InlineKeyboardButton(fancy("🛡️ ᴡʜʏ ᴛʀᴜsᴛ ᴜs?"), callback_data="help_trust")],
                [InlineKeyboardButton(fancy("📞 ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ"), callback_data="support")],
                [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
            ])
        )
    elif data == "help_howto":
        await query.edit_message_text(
            fancy("📖 **ʜᴏᴡ ᴛᴏ ᴜsᴇ**\n\n"
                  "1️⃣ ᴀᴅᴅ ᴄʀᴇᴅɪᴛs ᴠɪᴀ ᴜᴘɪ/ᴜsᴅᴛ/sᴛᴀʀs.\n"
                  "2️⃣ ɢᴏ ᴛᴏ sᴛᴏʀᴇ → sᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ.\n"
                  "3️⃣ ᴄʜᴏᴏsᴇ ᴀ ᴄᴏᴜɴᴛʀʏ → ᴛᴀᴘ ʙᴜʏ.\n"
                  "4️⃣ ʀᴇᴄᴇɪᴠᴇ ᴏᴛᴘ & 2ꜰᴀ ɪɴsᴛᴀɴᴛʟʏ.\n"
                  "5️⃣ ʀᴇ-ʀᴇǫᴜᴇsᴛ ᴏᴛᴘ ᴡɪᴛʜɪɴ 24ʜ."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="help")]
            ])
        )
    elif data == "help_otp":
        await query.edit_message_text(
            fancy("🔑 **ᴏᴛᴘ ᴛʀᴏᴜʙʟᴇsʜᴏᴏᴛɪɴɢ**\n\n"
                  "⚠️ ᴄᴏᴅᴇ ɴᴏᴛ ᴀʀʀɪᴠᴇᴅ?\n→ ʀᴇǫᴜᴇsᴛ ᴀɢᴀɪɴ.\n\n"
                  "❓ ᴡʜᴇʀᴇ ɪs 2ꜰᴀ?\n→ ᴄʜᴇᴄᴋ ᴏʀᴅᴇʀ ᴅᴇᴛᴀɪʟs."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="help")]
            ])
        )
    elif data == "help_payment":
        await query.edit_message_text(
            fancy("💳 **ᴘᴀʏᴍᴇɴᴛ ʜᴇʟᴘ**\n\n"
                  "| ᴍᴇᴛʜᴏᴅ | ᴛɪᴍᴇ |\n"
                  "| ᴜᴘɪ / Qʀ | ɪɴsᴛᴀɴᴛ (1–2 ᴍɪɴ) |\n"
                  "| ᴜsᴅᴛ (BSC) | 1–3 ʙʟᴏᴄᴋ ᴄᴏɴғɪʀᴍs |\n"
                  "| ᴛᴇʟᴇɢʀᴀᴍ sᴛᴀʀs | ɪɴsᴛᴀɴᴛ |\n\n"
                  "✔ ᴘᴀɪᴅ ʙᴜᴛ ᴄʀᴇᴅɪᴛs ɴᴏᴛ ᴀᴅᴅᴇᴅ?\n→ ᴛᴀᴘ 'ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ'."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="help")]
            ])
        )
    elif data == "help_trust":
        await query.edit_message_text(
            fancy("🛡️ **ᴡʜʏ ᴛʀᴜsᴛ ᴏᴛᴘ ʙᴏᴛ?**\n\n🔹 100% ᴀᴜᴛᴏᴍᴀᴛᴇᴅ\n🔹 ɪɴsᴛᴀɴᴛ ᴏᴛᴘ ғᴏʀᴡᴀʀᴅɪɴɢ\n🔹 ɴᴏ ʜᴜᴍᴀɴ sᴇᴇs sᴇssɪᴏɴ ᴅᴀᴛᴀ"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="help")]
            ])
        )
    elif data == "support":
        support_link = await get_setting("support_link")
        if support_link:
            await query.edit_message_text(
                fancy("📞 **ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ**\n\nᴛᴀᴘ ʙᴇʟᴏᴡ ᴛᴏ ᴄʜᴀᴛ."),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📞 sᴜᴘᴘᴏʀᴛ", url=support_link)],
                    [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="help")]
                ])
            )
        else:
            await query.edit_message_text(
                fancy("📞 **sᴜᴘᴘᴏʀᴛ**\n\nɴᴏ sᴜᴘᴘᴏʀᴛ ʟɪɴᴋ ᴄᴏɴғɪɢᴜʀᴇᴅ."),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="help")]
                ])
            )

    # ── WHATSAPP ──────────────────────────────────
    elif data == "whatsapp":
        await query.edit_message_text(
            fancy("💬 **ᴡʜᴀᴛsᴀᴘᴘ**\n\nᴄᴏɴᴛᴀᴄᴛ ᴏᴜʀ ᴡʜᴀᴛsᴀᴘᴘ sᴜᴘᴘᴏʀᴛ ғᴏʀ ᴀssɪsᴛᴀɴᴄᴇ."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
            ])
        )

    # ─── ADMIN ─────────────────────────────────────
    elif data == "admin":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        is_owner = (user_id == OWNER_ID)
        await query.edit_message_text(
            fancy("⚙️ **ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ**\n\nsᴇʟᴇᴄᴛ ᴀᴄᴛɪᴏɴ:"),
            reply_markup=get_admin_menu(is_owner)
        )

    elif data == "astats":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        total_users = await users_col.count_documents({})
        total_acc = await accounts_col.count_documents({})
        avail_acc = await accounts_col.count_documents({"status":"available"})
        total_orders = await orders_col.count_documents({})
        pending_deps = await deposits_col.count_documents({"status":"pending"})
        banned = await users_col.count_documents({"is_banned":True})
        rev_pipe = await deposits_col.aggregate([
            {"$match":{"status":"approved"}},
            {"$group":{"_id":None,"total":{"$sum":"$amount"}}}
        ]).to_list(1)
        revenue = rev_pipe[0]["total"] if rev_pipe else 0
        today = datetime.utcnow().replace(hour=0,minute=0,second=0,microsecond=0)
        new_today = await users_col.count_documents({"joined_at":{"$gte":today}})
        db_admins = await bot_admins_col.count_documents({"is_active":True})
        await query.edit_message_text(
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
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="admin")]
            ])
        )

    elif data == "upload_sessions":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        cats = await get_categories()
        rows = []
        for cat in cats:
            rows.append([InlineKeyboardButton(f"{cat['icon']} {cat['name']}", callback_data=f"upload_cat:{cat['name']}")])
        rows.append([InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="admin")])
        await query.edit_message_text(
            fancy("📦 **ᴜᴘʟᴏᴀᴅ sᴇssɪᴏɴs**\n\nsᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ:"),
            reply_markup=InlineKeyboardMarkup(rows)
        )

    elif data.startswith("upload_cat:"):
        cat_name = data.split(":",1)[1]
        c_list = await countries_col.find({"is_active":True}).to_list(50)
        rows = []
        for c in c_list:
            rows.append([InlineKeyboardButton(f"{c['flag']} {c['name']}", callback_data=f"upload_country:{c['code']}:{cat_name}")])
        rows.append([InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="admin")])
        await query.edit_message_text(
            fancy(f"📦 **ᴜᴘʟᴏᴀᴅ ᴛᴏ {cat_name}**\n\nsᴇʟᴇᴄᴛ ᴄᴏᴜɴᴛʀʏ:"),
            reply_markup=InlineKeyboardMarkup(rows)
        )

    elif data.startswith("upload_country:"):
        parts = data.split(":",2)
        if len(parts) < 3:
            await query.answer("❌ Error.", show_alert=True)
            return
        _, cc, cat_name = parts
        c = await countries_col.find_one({"code":cc})
        if not c:
            await query.answer("❌ Country not found.", show_alert=True)
            return
        user_states[user_id] = {
            "state":"waiting_zip",
            "country_code":cc,
            "country_name":c["name"],
            "price":float(c["price"]),
            "category":cat_name,
            "twofa_password":""
        }
        default_2fa = await get_setting("default_2fa","")
        hint = f"\n\n💡 ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ: `{default_2fa}`" if default_2fa else ""
        await query.edit_message_text(
            fancy(
                f"📦 **ᴜᴘʟᴏᴀᴅ sᴇssɪᴏɴs — {c['flag']} {c['name']} ({cat_name})**\n\n"
                "sᴛᴇᴘ 1 (ᴏᴘᴛɪᴏɴᴀʟ): sᴇɴᴅ 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ ᴀs ᴛᴇxᴛ.\n"
                f"ɪғ ɴᴏᴛ sᴇɴᴛ, ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ ᴡɪʟʟ ʙᴇ ᴜsᴇᴅ.{hint}\n"
                "sᴛᴇᴘ 2: sᴇɴᴅ ᴛʜᴇ **.ᴢɪᴘ** ᴏʀ **.sᴇssɪᴏɴ** ꜰɪʟᴇ."
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="admin")]
            ])
        )

    elif data == "manage_sessions":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        total = await accounts_col.count_documents({})
        avail = await accounts_col.count_documents({"status":"available"})
        sold = await accounts_col.count_documents({"status":"sold"})
        logout_ = await accounts_col.count_documents({"status":"logged_out"})
        pipe = await accounts_col.aggregate([
            {"$match":{"status":"available"}},
            {"$group":{"_id":"$country","count":{"$sum":1},"flag":{"$first":"$country_flag"}}},
            {"$sort":{"count":-1}}
        ]).to_list(20)
        by_country = "\n".join(
            f"  {r.get('flag','🌍')} {r['_id']}: `{r['count']}`"
            for r in pipe
        ) or "  (ᴇᴍᴘᴛʏ)"
        await query.edit_message_text(
            fancy(
                f"📋 **sᴇssɪᴏɴ ᴏᴠᴇʀᴠɪᴇᴡ**\n\n"
                f"ᴛᴏᴛᴀʟ: `{total}`\n"
                f"✅ ᴀᴠᴀɪʟᴀʙʟᴇ: `{avail}`\n"
                f"🔑 sᴏʟᴅ: `{sold}`\n"
                f"🔓 ʟᴏɢɢᴇᴅ ᴏᴜᴛ: `{logout_}`\n\n"
                f"**ᴀᴠᴀɪʟᴀʙʟᴇ ʙʏ ᴄᴏᴜɴᴛʀʏ:**\n{by_country}"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="admin")]
            ])
        )

    elif data == "pending_deposits":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        deps = await deposits_col.find({"status":"pending"}).sort("created_at",1).limit(10).to_list(10)
        if not deps:
            await query.edit_message_text(
                fancy("✅ ɴᴏ ᴘᴇɴᴅɪɴɢ ᴅᴇᴘᴏsɪᴛs."),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="admin")]
                ])
            )
            return
        for dep in deps:
            dep_id = str(dep["_id"])
            uid = dep["user_id"]
            amount = dep["amount"]
            created = dep["created_at"].strftime("%d %b %H:%M")
            await context.bot.send_message(
                user_id,
                fancy(f"💳 **ᴅᴇᴘᴏsɪᴛ ʀᴇǫᴜᴇsᴛ**\n• ᴜsᴇʀ ɪᴅ: `{uid}`\n• ᴀᴍᴏᴜɴᴛ: `₹{amount:.2f}`\n• ᴛɪᴍᴇ: {created}"),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("✅ ᴀᴘᴘʀᴏᴠᴇ"), callback_data=f"dep_approve:{dep_id}:{uid}:{amount}"),
                     InlineKeyboardButton(fancy("❌ ʀᴇᴊᴇᴄᴛ"), callback_data=f"dep_reject:{dep_id}:{uid}")]
                ])
            )
        await query.edit_message_text("📋 Pending deposits listed above.")

    elif data.startswith("dep_approve:"):
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        _, dep_id, uid_str, amount_str = data.split(":",3)
        uid = int(uid_str)
        amount = float(amount_str)
        claim = await deposits_col.update_one(
            {"_id":dep_id,"status":"pending"},
            {"$set":{"status":"approved","approved_at":datetime.utcnow(),"approved_by":user_id}}
        )
        if claim.matched_count == 0:
            await query.answer("⚠️ Already processed.", show_alert=True)
            return
        await users_col.update_one({"user_id":uid}, {"$inc":{"balance":amount,"withdrawable":amount}})
        buyer = await users_col.find_one({"user_id":uid})
        if buyer and buyer.get("referred_by"):
            pct = float(await get_setting("referral_percent",3.0))
            bonus = round(amount * pct / 100, 2)
            if bonus > 0:
                await users_col.update_one(
                    {"user_id":buyer["referred_by"]},
                    {"$inc":{"balance":bonus,"referral_earnings":bonus,"withdrawable":bonus}}
                )
                try:
                    await context.bot.send_message(
                        buyer["referred_by"],
                        fancy(f"💸 **ʀᴇꜰᴇʀʀᴀʟ ᴇᴀʀɴɪɴɢ!**\n+₹{bonus:.2f} ғʀᴏᴍ ᴅᴇᴘᴏsɪᴛ.")
                    )
                except:
                    pass
        try:
            await context.bot.send_message(uid, fancy(f"✅ **ᴅᴇᴘᴏsɪᴛ ᴀᴘᴘʀᴏᴠᴇᴅ!**\n`₹{amount:.2f}` ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ."))
        except:
            pass
        await query.edit_message_text(f"✅ ᴀᴘᴘʀᴏᴠᴇᴅ ₹{amount:.2f} ғᴏʀ ᴜsᴇʀ `{uid}`.")

    elif data.startswith("dep_reject:"):
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        _, dep_id, uid_str = data.split(":",2)
        uid = int(uid_str)
        dep_doc = await deposits_col.find_one_and_update(
            {"_id":dep_id,"status":"pending"},
            {"$set":{"status":"rejected","rejected_at":datetime.utcnow(),"rejected_by":user_id}},
            return_document=True
        )
        if dep_doc is None:
            await query.answer("⚠️ Already processed.", show_alert=True)
            return
        try:
            await context.bot.send_message(uid, fancy("❌ **ᴅᴇᴘᴏsɪᴛ ʀᴇᴊᴇᴄᴛᴇᴅ.**\nᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ."))
        except:
            pass
        await query.edit_message_text(f"❌ ʀᴇᴊᴇᴄᴛᴇᴅ ᴅᴇᴘᴏsɪᴛ ғᴏʀ ᴜsᴇʀ `{uid}`.")

    elif data == "broadcast":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        user_states[user_id] = {"state":"broadcast"}
        await query.edit_message_text(
            fancy("📢 **ʙʀᴏᴀᴅᴄᴀsᴛ**\n\nsᴇɴᴅ ᴛʜᴇ ᴍᴇssᴀɢᴇ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ᴛᴏ ᴀʟʟ ᴜsᴇʀs."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="admin")]
            ])
        )

    elif data == "asettings":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        upi = await get_setting("upi_id","Not set")
        uname_ = await get_setting("upi_name","Next Level Vault")
        sup = await get_setting("support_link","Not set")
        bn = await get_setting("bot_name","Next Level Vault")
        rb = await get_setting("referral_bonus",10)
        rp = await get_setting("referral_percent",3)
        md = await get_setting("min_deposit",10)
        wa = "✅" if await get_setting("whatsapp_enabled") else "❌"
        photo = "✅" if await get_setting("welcome_photo") else "❌"
        delay = await get_setting("auto_verify_delay",10)
        webhook = await get_setting("payment_webhook_url","Not set")
        default2fa = await get_setting("default_2fa","")
        await query.edit_message_text(
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
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("🤖 ʙᴏᴛ ɴᴀᴍᴇ"), callback_data="set_botname"),
                 InlineKeyboardButton(fancy("💳 ᴜᴘɪ ɪᴅ"), callback_data="set_upi")],
                [InlineKeyboardButton(fancy("👤 ᴜᴘɪ ɴᴀᴍᴇ"), callback_data="set_upiname"),
                 InlineKeyboardButton(fancy("📞 sᴜᴘᴘᴏʀᴛ"), callback_data="set_support")],
                [InlineKeyboardButton(fancy("🎁 ʀᴇꜰ ʙᴏɴᴜs"), callback_data="set_ref_bonus"),
                 InlineKeyboardButton(fancy("📈 ʀᴇꜰ %"), callback_data="set_ref_pct")],
                [InlineKeyboardButton(fancy("🔢 ᴍɪɴ ᴅᴇᴘᴏsɪᴛ"), callback_data="set_min_dep")],
                [InlineKeyboardButton(fancy("💬 ᴛᴏɢɢʟᴇ ᴡʜᴀᴛsᴀᴘᴘ"), callback_data="toggle_whatsapp"),
                 InlineKeyboardButton(fancy("🖼️ sᴇᴛ ᴡᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ"), callback_data="set_welcome_photo")],
                [InlineKeyboardButton(fancy("⏳ ᴀᴜᴛᴏ ᴠᴇʀɪғʏ ᴅᴇʟᴀʏ"), callback_data="set_verify_delay"),
                 InlineKeyboardButton(fancy("🌐 ᴘᴀʏᴍᴇɴᴛ ᴡᴇʙʜᴏᴏᴋ"), callback_data="set_webhook")],
                [InlineKeyboardButton(fancy("🔐 ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ"), callback_data="set_default_2fa")],
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="admin")]
            ])
        )

    elif data in ("set_botname","set_upi","set_upiname","set_support",
                  "set_ref_bonus","set_ref_pct","set_min_dep",
                  "toggle_whatsapp","set_welcome_photo","set_verify_delay",
                  "set_webhook","set_default_2fa"):
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        _prompts = {
            "set_botname":("setting_botname","🤖 sᴇɴᴅ ɴᴇᴡ ʙᴏᴛ ɴᴀᴍᴇ:"),
            "set_upi":("setting_upi","💳 sᴇɴᴅ ɴᴇᴡ ᴜᴘɪ ɪᴅ:"),
            "set_upiname":("setting_upiname","👤 sᴇɴᴅ ᴜᴘɪ ᴅɪsᴘʟᴀʏ ɴᴀᴍᴇ:"),
            "set_support":("setting_support","📞 sᴇɴᴅ sᴜᴘᴘᴏʀᴛ ʟɪɴᴋ:"),
            "set_ref_bonus":("setting_ref_bonus","🎁 sᴇɴᴅ ʀᴇꜰᴇʀʀᴀʟ ᴊᴏɪɴ ʙᴏɴᴜs (₹):"),
            "set_ref_pct":("setting_ref_pct","📈 sᴇɴᴅ ʀᴇꜰᴇʀʀᴀʟ ᴅᴇᴘᴏsɪᴛ %:"),
            "set_min_dep":("setting_min_dep","🔢 sᴇɴᴅ ᴍɪɴɪᴍᴜᴍ ᴅᴇᴘᴏsɪᴛ (₹):"),
            "toggle_whatsapp":("whatsapp_toggle",None),
            "set_welcome_photo":("welcome_photo_set","🖼️ sᴇɴᴅ ᴛʜᴇ ᴘʜᴏᴛᴏ ᴛᴏ ᴜsᴇ ᴀs ᴡᴇʟᴄᴏᴍᴇ ɪᴍᴀɢᴇ."),
            "set_verify_delay":("setting_verify_delay","⏳ sᴇɴᴅ ᴅᴇʟᴀʏ ɪɴ sᴇᴄᴏɴᴅs (ᴇ.ɢ. 10):"),
            "set_webhook":("setting_webhook","🌐 sᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ᴡᴇʙʜᴏᴏᴋ ᴜʀʟ:"),
            "set_default_2fa":("setting_default_2fa","🔐 sᴇɴᴅ ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ:")
        }
        sk, prompt = _prompts[data]
        if data == "toggle_whatsapp":
            current = await get_setting("whatsapp_enabled", False)
            await set_setting("whatsapp_enabled", not current)
            await query.answer(f"✅ WhatsApp {'enabled' if not current else 'disabled'}")
            await query.edit_message_text(
                fancy("💬 **ᴡʜᴀᴛsᴀᴘᴘ** ᴛᴏɢɢʟᴇᴅ."),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="asettings")]
                ])
            )
            return
        elif data == "set_welcome_photo":
            user_states[user_id] = {"state":"welcome_photo_set"}
            await query.edit_message_text(
                prompt,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="asettings")]
                ])
            )
            return
        user_states[user_id] = {"state":sk}
        await query.edit_message_text(
            fancy(prompt),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="asettings")]
            ])
        )

    elif data == "acountries":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        c_list = await countries_col.find({}).to_list(50)
        rows = []
        for c in c_list:
            em = "✅" if c.get("is_active") else "❌"
            rows.append([InlineKeyboardButton(f"{em} {c['flag']} {c['name']} — {c['price']} ᴄʀ", callback_data=f"ctoggle:{c['code']}")])
        rows.append([InlineKeyboardButton(fancy("➕ ᴀᴅᴅ ᴄᴏᴜɴᴛʀʏ"), callback_data="add_country"),
                     InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="admin")])
        await query.edit_message_text(
            fancy("🌍 **ᴄᴏᴜɴᴛʀɪᴇs** (ᴛᴀᴘ ᴛᴏ ᴛᴏɢɢʟᴇ):"),
            reply_markup=InlineKeyboardMarkup(rows)
        )

    elif data.startswith("ctoggle:"):
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        code = data.split(":")[1]
        c = await countries_col.find_one({"code":code})
        new = not c.get("is_active", True)
        await countries_col.update_one({"code":code}, {"$set":{"is_active":new}})
        await query.answer(f"{'Enabled' if new else 'Disabled'} {code}")
        # refresh list
        c_list = await countries_col.find({}).to_list(50)
        rows = []
        for c in c_list:
            em = "✅" if c.get("is_active") else "❌"
            rows.append([InlineKeyboardButton(f"{em} {c['flag']} {c['name']} — {c['price']} ᴄʀ", callback_data=f"ctoggle:{c['code']}")])
        rows.append([InlineKeyboardButton(fancy("➕ ᴀᴅᴅ ᴄᴏᴜɴᴛʀʏ"), callback_data="add_country"),
                     InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="admin")])
        await query.edit_message_text(
            fancy("🌍 **ᴄᴏᴜɴᴛʀɪᴇs** (ᴛᴀᴘ ᴛᴏ ᴛᴏɢɢʟᴇ):"),
            reply_markup=InlineKeyboardMarkup(rows)
        )

    elif data == "add_country":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        user_states[user_id] = {"state":"add_country"}
        await query.edit_message_text(
            fancy("🌍 **ᴀᴅᴅ ᴄᴏᴜɴᴛʀʏ**\n\nsᴇɴᴅ:\n`CODE | Name | Flag | Price`\nᴇxᴀᴍᴘʟᴇ: `TR | Turkey | 🇹🇷 | 28`"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="acountries")]
            ])
        )

    elif data == "ausers":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        user_states[user_id] = {"state":"search_user"}
        await query.edit_message_text(
            fancy("👤 **ᴜsᴇʀ ʟᴏᴏᴋᴜᴘ**\n\nsᴇɴᴅ ᴛʜᴇ ᴛᴇʟᴇɢʀᴀᴍ ᴜsᴇʀ ɪᴅ:"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("➕ ᴀᴅᴅ ʙᴀʟᴀɴᴄᴇ"), callback_data="admin_add_bal"),
                 InlineKeyboardButton(fancy("🚫 ʙᴀɴ/ᴜɴʙᴀɴ"), callback_data="admin_ban")],
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="admin")]
            ])
        )

    elif data == "admin_add_bal":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        user_states[user_id] = {"state":"add_bal_uid"}
        await query.edit_message_text(
            fancy("💰 **ᴀᴅᴅ ʙᴀʟᴀɴᴄᴇ**\n\nsᴇɴᴅ ᴜsᴇʀ ɪᴅ:"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="admin")]
            ])
        )

    elif data == "admin_ban":
        if not await is_admin(user_id):
            await query.answer("❌ Access denied.", show_alert=True)
            return
        user_states[user_id] = {"state":"ban_uid"}
        await query.edit_message_text(
            fancy("🚫 **ʙᴀɴ/ᴜɴʙᴀɴ**\n\nsᴇɴᴅ ᴜsᴇʀ ɪᴅ:"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="admin")]
            ])
        )

    elif data == "manage_admins":
        if user_id != OWNER_ID:
            await query.answer("❌ Owner only!", show_alert=True)
            return
        admins = await bot_admins_col.find({"is_active":True}).to_list(50)
        rows = []
        for a in admins:
            rows.append([InlineKeyboardButton(f"🔴 ʀᴇᴍᴏᴠᴇ {a.get('name', a['telegram_id'])}", callback_data=f"rm_admin:{a['telegram_id']}")])
        rows.append([InlineKeyboardButton(fancy("➕ ᴀᴅᴅ ᴀᴅᴍɪɴ"), callback_data="add_admin"),
                     InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="admin")])
        await query.edit_message_text(
            fancy(f"🔑 **ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs**\n\nᴏᴡɴᴇʀ: `{OWNER_ID}`\nᴇxᴛʀᴀ ᴀᴅᴍɪɴs: {len(admins)}"),
            reply_markup=InlineKeyboardMarkup(rows)
        )

    elif data == "add_admin":
        if user_id != OWNER_ID:
            await query.answer("❌ Owner only!", show_alert=True)
            return
        user_states[user_id] = {"state":"add_admin"}
        await query.edit_message_text(
            fancy("🔑 **ᴀᴅᴅ ᴀᴅᴍɪɴ**\n\nsᴇɴᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴜsᴇʀ ɪᴅ:"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="manage_admins")]
            ])
        )

    elif data.startswith("rm_admin:"):
        if user_id != OWNER_ID:
            await query.answer("❌ Owner only!", show_alert=True)
            return
        rm_id = int(data.split(":")[1])
        await bot_admins_col.update_one({"telegram_id":rm_id}, {"$set":{"is_active":False}})
        await query.answer(f"Removed admin {rm_id}")
        try:
            await context.bot.send_message(rm_id, "🔑 Your admin access has been removed.")
        except:
            pass
        await query.edit_message_text(
            fancy("✅ Admin removed."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs"), callback_data="manage_admins")]
            ])
        )

    elif data == "balance":
        user = await get_or_create_user(user_id)
        bal = float(user.get("balance",0))
        spent = 0.0
        async for o in orders_col.find({"user_id":user_id,"status":{"$nin":["cancelled"]}}):
            spent += float(o.get("amount",0))
        await query.edit_message_text(
            fancy(f"💰 **ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ**\n\nᴀᴠᴀɪʟᴀʙʟᴇ: `{bal:.2f} ᴄʀ`\nᴛᴏᴛᴀʟ sᴘᴇɴᴛ: `{spent:.2f} ᴄʀ`"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("💳 ᴀᴅᴅ ᴄʀᴇᴅɪᴛs"), callback_data="deposit")],
                [InlineKeyboardButton(fancy("🏠 ʜᴏᴍᴇ"), callback_data="main_menu")]
            ])
        )

    else:
        await query.answer("Unknown action.")

# ─── MESSAGE HANDLER ──────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    state_data = user_states.get(user_id)
    if not state_data:
        return

    state = state_data.get("state") if isinstance(state_data, dict) else state_data

    if state == "custom_deposit":
        try:
            amount = float(text.strip().replace(",",""))
        except ValueError:
            await update.message.reply_text(fancy("❌ ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))
            return
        min_d = float(await get_setting("min_deposit",10.0))
        if amount < min_d:
            await update.message.reply_text(fancy(f"❌ ᴍɪɴɪᴍᴜᴍ ᴅᴇᴘᴏsɪᴛ ɪs ₹{min_d:.0f}."))
            return
        credits = int(amount // 1)
        deposit_id = str(uuid.uuid4())[:8]
        await deposits_col.insert_one({
            "deposit_id":deposit_id,"user_id":user_id,"amount":amount,
            "credits":credits,"method":"upi","status":"pending",
            "created_at":datetime.utcnow()
        })
        user_states[user_id] = {"deposit_id":deposit_id,"credits":credits,"amount":amount}
        upi_id = await get_setting("upi_id")
        upi_name = await get_setting("upi_name","Next Level Vault")
        qr = _make_upi_qr(upi_id, amount, upi_name)
        msg = fancy(f"💳 **ᴜᴘɪ ᴘᴀʏᴍᴇɴᴛ**\n\n• ᴀᴍᴏᴜɴᴛ: `₹{amount:.2f}`\n• ᴄʀᴇᴅɪᴛs: `+{credits}`\n• ᴜᴘɪ ɪᴅ: `{upi_id}`\n\nsᴄᴀɴ Qʀ ᴏʀ ᴜsᴇ 'ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ᴀᴘᴘ'.")
        if qr:
            await update.message.reply_photo(photo=io.BytesIO(qr), caption=msg)
        await update.message.reply_text(
            msg,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ"), callback_data="deposit_paid")],
                [InlineKeyboardButton(fancy("🔄 ᴄʜᴇᴄᴋ ᴘᴀʏᴍᴇɴᴛ"), callback_data="check_payment")],
                [InlineKeyboardButton(fancy("◀️ ʙᴀᴄᴋ"), callback_data="deposit")]
            ])
        )
        user_states.pop(user_id, None)

    elif state == "waiting_zip":
        if not update.message.document and not update.message.photo:
            twofa = text.strip()
            user_states[user_id] = {**state_data, "twofa_password":twofa}
            await update.message.reply_text(
                fancy(f"🔐 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ sᴀᴠᴇᴅ: `{twofa}`\n\nɴᴏᴡ sᴇɴᴅ ᴛʜᴇ **.ᴢɪᴘ** ᴏʀ **.sᴇssɪᴏɴ** ꜰɪʟᴇ."),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(fancy("❌ ᴄᴀɴᴄᴇʟ"), callback_data="admin")]
                ])
            )
            return
        file = await update.message.document.get_file()
        file_bytes = await file.download_as_bytearray()
        if update.message.document.file_name.lower().endswith('.session'):
            ss = await _session_file_to_string(bytes(file_bytes))
            if not ss:
                await update.message.reply_text("❌ Invalid session file.")
                user_states.pop(user_id, None)
                return
            phone = _phone_from_filename(update.message.document.file_name)
            flag = await get_country_flag(state_data["country_code"])
            await accounts_col.insert_one({
                "phone":phone,"session_string":ss,
                "country":state_data["country_name"],
                "country_code":state_data["country_code"],
                "country_flag":flag,
                "price":state_data["price"],
                "category":state_data["category"],
                "twofa_password":state_data.get("twofa_password", await get_setting("default_2fa","")),
                "status":"available","added_at":datetime.utcnow()
            })
            if acc_mgr:
                await acc_mgr.add_client(phone, ss)
            await update.message.reply_text(fancy(f"✅ **ꜰɪʟᴇ ᴜᴘʟᴏᴀᴅᴇᴅ**\n`{phone}` ᴀᴅᴅᴇᴅ."))
            user_states.pop(user_id, None)
            return

        try:
            zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        except Exception as e:
            await update.message.reply_text(f"❌ Invalid ZIP: {e}")
            user_states.pop(user_id, None)
            return

        twofa = state_data.get("twofa_password","")
        try:
            info = zf.getinfo("2fa.txt")
            twofa = zf.read(info).decode().strip()
        except:
            pass
        if not twofa:
            twofa = await get_setting("default_2fa","")

        all_names = [n for n in zf.namelist() if n.lower().endswith(".session")]
        if not all_names:
            await update.message.reply_text("❌ No .session files found in ZIP.")
            user_states.pop(user_id, None)
            return

        added = 0
        skipped = 0
        errors = []
        async def process_one(name):
            nonlocal added, skipped
            phone = _phone_from_filename(name)
            try:
                raw_bytes = zf.read(name)
            except:
                errors.append(phone); skipped+=1; return
            ss = await _session_file_to_string(raw_bytes)
            if not ss:
                errors.append(phone); skipped+=1; return
            flag = await get_country_flag(state_data["country_code"])
            await accounts_col.insert_one({
                "phone":phone,"session_string":ss,
                "country":state_data["country_name"],
                "country_code":state_data["country_code"],
                "country_flag":flag,
                "price":state_data["price"],
                "category":state_data["category"],
                "twofa_password":twofa,
                "status":"available","added_at":datetime.utcnow()
            })
            if acc_mgr:
                await acc_mgr.add_client(phone, ss)
            added += 1

        tasks = [process_one(n) for n in all_names]
        await asyncio.gather(*tasks)
        result = fancy(f"📦 **ᴜᴘʟᴏᴀᴅ ᴄᴏᴍᴘʟᴇᴛᴇ**\n\n✅ ᴀᴅᴅᴇᴅ: `{added}`\n⏭️ sᴋɪᴘᴘᴇᴅ: `{skipped}`\n")
        if errors:
            shown = errors[:5]
            result += f"❌ ꜰᴀɪʟᴇᴅ ({len(errors)}): `{', '.join(shown)}`"
        await update.message.reply_text(result, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy("◀️ ᴀᴅᴍɪɴ"), callback_data="admin")]
        ]))
        user_states.pop(user_id, None)
        zf.close()

    elif state == "broadcast":
        user_states.pop(user_id, None)
        prog = await update.message.reply_text(fancy("📢 ʙʀᴏᴀᴅᴄᴀsᴛɪɴɢ…"))
        count = 0
        fail = 0
        async for u in users_col.find({}, {"user_id":1}):
            try:
                await context.bot.send_message(u["user_id"], text)
                count += 1
            except:
                fail += 1
            if (count+fail) % 50 == 0:
                try:
                    await prog.edit_text(f"📢 ʙʀᴏᴀᴅᴄᴀsᴛɪɴɢ… {count} sᴇɴᴛ, {fail} ꜰᴀɪʟᴇᴅ")
                except:
                    pass
            await asyncio.sleep(0.05)
        await prog.edit_text(fancy(f"✅ ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴏᴍᴘʟᴇᴛᴇ — {count} sᴇɴᴛ, {fail} ꜰᴀɪʟᴇᴅ."))

    elif state == "setting_botname":
        await set_setting("bot_name", text.strip())
        user_states.pop(user_id, None)
        await update.message.reply_text(fancy("✅ ʙᴏᴛ ɴᴀᴍᴇ ᴜᴘᴅᴀᴛᴇᴅ."), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
        ]))
    elif state == "setting_upi":
        await set_setting("upi_id", text.strip())
        user_states.pop(user_id, None)
        await update.message.reply_text(fancy("✅ ᴜᴘɪ ɪᴅ ᴜᴘᴅᴀᴛᴇᴅ."), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
        ]))
    elif state == "setting_upiname":
        await set_setting("upi_name", text.strip())
        user_states.pop(user_id, None)
        await update.message.reply_text(fancy("✅ ᴜᴘɪ ɴᴀᴍᴇ ᴜᴘᴅᴀᴛᴇᴅ."), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
        ]))
    elif state == "setting_support":
        await set_setting("support_link", text.strip())
        user_states.pop(user_id, None)
        await update.message.reply_text(fancy("✅ sᴜᴘᴘᴏʀᴛ ʟɪɴᴋ ᴜᴘᴅᴀᴛᴇᴅ."), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
        ]))
    elif state == "setting_ref_bonus":
        try:
            val = float(text.strip())
            await set_setting("referral_bonus", val)
            user_states.pop(user_id, None)
            await update.message.reply_text(fancy(f"✅ ʀᴇꜰᴇʀʀᴀʟ ʙᴏɴᴜs sᴇᴛ ᴛᴏ ₹{val:.0f}."), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
            ]))
        except ValueError:
            await update.message.reply_text(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))
    elif state == "setting_ref_pct":
        try:
            val = float(text.strip())
            await set_setting("referral_percent", val)
            user_states.pop(user_id, None)
            await update.message.reply_text(fancy(f"✅ ʀᴇꜰᴇʀʀᴀʟ % sᴇᴛ ᴛᴏ {val:.1f}%."), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
            ]))
        except ValueError:
            await update.message.reply_text(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))
    elif state == "setting_min_dep":
        try:
            val = float(text.strip())
            await set_setting("min_deposit", val)
            user_states.pop(user_id, None)
            await update.message.reply_text(fancy(f"✅ ᴍɪɴɪᴍᴜᴍ ᴅᴇᴘᴏsɪᴛ sᴇᴛ ᴛᴏ ₹{val:.0f}."), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
            ]))
        except ValueError:
            await update.message.reply_text(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))
    elif state == "setting_verify_delay":
        try:
            val = int(text.strip())
            await set_setting("auto_verify_delay", val)
            user_states.pop(user_id, None)
            await update.message.reply_text(fancy(f"✅ ᴀᴜᴛᴏ ᴠᴇʀɪғʏ ᴅᴇʟᴀʏ sᴇᴛ ᴛᴏ {val}s."), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
            ]))
        except ValueError:
            await update.message.reply_text(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."))
    elif state == "setting_webhook":
        await set_setting("payment_webhook_url", text.strip())
        user_states.pop(user_id, None)
        await update.message.reply_text(fancy("✅ ᴘᴀʏᴍᴇɴᴛ ᴡᴇʙʜᴏᴏᴋ ᴜʀʟ sᴇᴛ."), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
        ]))
    elif state == "setting_default_2fa":
        await set_setting("default_2fa", text.strip())
        user_states.pop(user_id, None)
        await update.message.reply_text(fancy("✅ ᴅᴇꜰᴀᴜʟᴛ 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ sᴇᴛ."), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
        ]))
    elif state == "welcome_photo_set":
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            await set_setting("welcome_photo", file_id)
            user_states.pop(user_id, None)
            await update.message.reply_text(fancy("🖼️ ᴡᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ sᴇᴛ!"), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ sᴇᴛᴛɪɴɢs"), callback_data="asettings")]
            ]))
        else:
            await update.message.reply_text(fancy("❌ ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴘʜᴏᴛᴏ."))
    elif state == "add_country":
        try:
            parts = [p.strip() for p in text.strip().split("|")]
            code, name, flag_, price_str = parts[0].upper(), parts[1], parts[2], parts[3]
            price_val = float(price_str)
            existing = await countries_col.find_one({"code":code})
            if existing:
                await countries_col.update_one({"code":code}, {"$set":{"name":name,"flag":flag_,"price":price_val,"is_active":True}})
                msg = f"♻️ **{name}** ᴜᴘᴅᴀᴛᴇᴅ (₹{price_val:.0f})."
            else:
                await countries_col.insert_one({"code":code,"name":name,"flag":flag_,"price":price_val,"is_active":True})
                msg = f"✅ **{name}** ᴀᴅᴅᴇᴅ (₹{price_val:.0f})."
            user_states.pop(user_id, None)
            await update.message.reply_text(fancy(msg), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ᴄᴏᴜɴᴛʀɪᴇs"), callback_data="acountries")]
            ]))
        except Exception:
            await update.message.reply_text(fancy("❌ ᴡʀᴏɴɢ ꜰᴏʀᴍᴀᴛ. ᴜsᴇ:\n`CODE | Name | Flag | Price`"))
    elif state == "search_user":
        try:
            tid = int(text.strip())
            target = await users_col.find_one({"user_id":tid})
            if not target:
                await update.message.reply_text(fancy("❌ ᴜsᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ."))
                return
            o_count = await orders_col.count_documents({"user_id":tid})
            d_count = await deposits_col.count_documents({"user_id":tid})
            banned_ = "🚫 ʏᴇs" if target.get("is_banned") else "✅ ɴᴏ"
            await update.message.reply_text(fancy(f"👤 **ᴜsᴇʀ ɪɴꜰᴏ**\n\n• ɪᴅ: `{tid}`\n• ʙᴀʟᴀɴᴄᴇ: `₹{target.get('balance',0):.2f}`\n• ᴏʀᴅᴇʀs: `{o_count}`\n• ᴅᴇᴘᴏsɪᴛs: `{d_count}`\n• ʙᴀɴɴᴇᴅ: {banned_}\n• ᴊᴏɪɴᴇᴅ: {target.get('joined_at','?')}"))
            user_states.pop(user_id, None)
        except ValueError:
            await update.message.reply_text(fancy("❌ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ."))
    elif state == "add_bal_uid":
        try:
            tid = int(text.strip())
            user_states[user_id] = {"state":"add_bal_amount","target_id":tid}
            await update.message.reply_text(fancy(f"💰 ʜᴏᴡ ᴍᴜᴄʜ ᴛᴏ ᴀᴅᴅ ғᴏʀ ᴜsᴇʀ `{tid}`? (₹)"))
        except ValueError:
            await update.message.reply_text(fancy("❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ."))
    elif state == "add_bal_amount":
        try:
            amount = float(text.strip())
            tid = state_data["target_id"]
            await users_col.update_one({"user_id":tid}, {"$inc":{"balance":amount,"withdrawable":amount}})
            try:
                await context.bot.send_message(tid, fancy(f"💰 **₹{amount:.0f} ᴀᴅᴅᴇᴅ** ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ ʙʏ ᴀᴅᴍɪɴ!"))
            except:
                pass
            user_states.pop(user_id, None)
            await update.message.reply_text(fancy(f"✅ ₹{amount:.0f} ᴀᴅᴅᴇᴅ ᴛᴏ ᴜsᴇʀ `{tid}`."), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ᴀᴅᴍɪɴ"), callback_data="admin")]
            ]))
        except ValueError:
            await update.message.reply_text(fancy("❌ ɪɴᴠᴀʟɪᴅ ᴀᴍᴏᴜɴᴛ."))
    elif state == "ban_uid":
        try:
            tid = int(text.strip())
            target = await users_col.find_one({"user_id":tid})
            if not target:
                await update.message.reply_text(fancy("❌ ᴜsᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ."))
                user_states.pop(user_id, None)
                return
            new_ban = not target.get("is_banned", False)
            await users_col.update_one({"user_id":tid}, {"$set":{"is_banned":new_ban}})
            action = "ʙᴀɴɴᴇᴅ" if new_ban else "ᴜɴʙᴀɴɴᴇᴅ"
            try:
                await context.bot.send_message(tid, fancy("🚫 ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ **ʙᴀɴɴᴇᴅ** ғʀᴏᴍ ᴛʜɪs ʙᴏᴛ." if new_ban else "✅ ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ **ᴜɴʙᴀɴɴᴇᴅ**. ᴡᴇʟᴄᴏᴍᴇ ʙᴀᴄᴋ!"))
            except:
                pass
            user_states.pop(user_id, None)
            await update.message.reply_text(fancy(f"✅ ᴜsᴇʀ `{tid}` ʜᴀs ʙᴇᴇɴ **{action}**."), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ᴀᴅᴍɪɴ"), callback_data="admin")]
            ]))
        except ValueError:
            await update.message.reply_text(fancy("❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ."))
    elif state == "add_admin":
        try:
            new_id = int(text.strip())
            try:
                entity = await context.bot.get_chat(new_id)
                name_ = entity.first_name or str(new_id)
                uname_ = entity.username
            except:
                name_ = str(new_id)
                uname_ = None
            existing = await bot_admins_col.find_one({"telegram_id":new_id})
            if existing:
                await bot_admins_col.update_one({"telegram_id":new_id}, {"$set":{"is_active":True}})
            else:
                await bot_admins_col.insert_one({
                    "telegram_id":new_id,"name":name_,"username":uname_,
                    "is_active":True,"added_by":user_id,"added_at":datetime.utcnow()
                })
            user_states.pop(user_id, None)
            await update.message.reply_text(fancy(f"✅ **ᴀᴅᴍɪɴ ᴀᴅᴅᴇᴅ:** {name_} (`{new_id}`)"), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(fancy("◀️ ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs"), callback_data="manage_admins")]
            ]))
            try:
                await context.bot.send_message(new_id, fancy("🔑 ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ ɢʀᴀɴᴛᴇᴅ **ᴀᴅᴍɪɴ ᴀᴄᴄᴇss** ᴛᴏ ᴛʜᴇ ʙᴏᴛ!"))
            except:
                pass
        except ValueError:
            await update.message.reply_text(fancy("❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ."))
    else:
        pass

# ─── AUTO VERIFY ──────────────────────────────
async def auto_verify_payment(user_id: int, deposit_id: str, delay: int):
    await asyncio.sleep(delay)
    dep = await deposits_col.find_one({"deposit_id":deposit_id,"status":"pending"})
    if not dep:
        return
    webhook_url = await get_setting("payment_webhook_url")
    if webhook_url:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json={"deposit_id":deposit_id,"user_id":user_id}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("verified"):
                            await approve_deposit(deposit_id)
                            return
        except Exception as e:
            log.error(f"Webhook call failed: {e}")
    await approve_deposit(deposit_id)

async def approve_deposit(deposit_id: str):
    dep = await deposits_col.find_one({"deposit_id":deposit_id,"status":"pending"})
    if not dep:
        return
    await deposits_col.update_one({"deposit_id":deposit_id}, {"$set":{"status":"approved","approved_at":datetime.utcnow()}})
    uid = dep["user_id"]
    amount = dep["amount"]
    credits = dep.get("credits",0)
    await users_col.update_one({"user_id":uid}, {"$inc":{"balance":amount,"withdrawable":amount}})
    buyer = await users_col.find_one({"user_id":uid})
    if buyer and buyer.get("referred_by"):
        pct = float(await get_setting("referral_percent",3.0))
        bonus = round(amount * pct / 100, 2)
        if bonus > 0:
            await users_col.update_one(
                {"user_id":buyer["referred_by"]},
                {"$inc":{"balance":bonus,"referral_earnings":bonus,"withdrawable":bonus}}
            )
            try:
                await application.bot.send_message(
                    buyer["referred_by"],
                    fancy(f"💸 **ʀᴇꜰᴇʀʀᴀʟ ᴇᴀʀɴɪɴɢ!**\n+₹{bonus:.2f} ғʀᴏᴍ ᴅᴇᴘᴏsɪᴛ.")
                )
            except:
                pass
    try:
        await application.bot.send_message(uid, fancy(f"✅ **ᴘᴀʏᴍᴇɴᴛ ᴄᴏɴғɪʀᴍᴇᴅ!**\n`{credits} ᴄʀᴇᴅɪᴛs` ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ."))
    except:
        pass

# ─── SELF-PING ──────────────────────────────────
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
                log.info("[self-ping] Ping sent.")
        except Exception as e:
            log.warning(f"[self-ping] Failed: {e}")

# ─── HEALTH SERVER ──────────────────────────────
import threading
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_web = FastAPI(title="Next Level Vault")

@_web.get("/")
async def health():
    return {"status":"ok","bot":"Next Level Vault is running"}

@_web.get("/ping")
async def ping():
    return {"pong":True}

@_web.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    body = await request.json()
    event = body.get("event")
    if event == "payment.captured":
        deposit_id = body.get("payload",{}).get("payment",{}).get("entity",{}).get("notes",{}).get("deposit_id")
        if deposit_id:
            await approve_deposit(deposit_id)
            return {"status":"approved"}
    return {"status":"ok"}

def start_health_server():
    port = int(os.getenv("PORT", 8080))
    log.info(f"[health] FastAPI listening on port {port}")
    uvicorn.run(_web, host="0.0.0.0", port=port, log_level="warning")

# ─── MAIN ──────────────────────────────────────
async def main():
    global acc_mgr, application
    await init_db()

    threading.Thread(target=start_health_server, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL | filters.PHOTO, handle_message))

    acc_mgr = account_manager.AccountManager(accounts_col, application.bot, API_ID, API_HASH, pending_otp)
    await acc_mgr.load_all()

    if RENDER_EXTERNAL_URL:
        asyncio.create_task(self_ping())

    log.info("🚀 Starting PTB bot with all features...")
    await application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
