import re
import asyncio
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta
from telethon import TelegramClient, events, Button, types
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError

# ─── Fancy text (small caps / ᴍʏsᴇʟғ) ──────────────────────────────
_SMALL_CAPS = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ',
    'f': 'ғ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ',
    'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ',
    'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ',
    'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ',
    'z': 'ᴢ'
}
def _fancy(text: str) -> str:
    return ''.join(_SMALL_CAPS.get(ch.lower(), ch) for ch in text)

_BUTTON_NORMAL = {value: key.upper() for key, value in _SMALL_CAPS.items()}
def _button_label(text: str) -> str:
    return ''.join(_BUTTON_NORMAL.get(ch, ch) for ch in text)

# FIXED: Use Button.inline with style (supports colored buttons)
def color_btn(text, data, style="default"):
    if style == "default":
        style = None
    return Button.inline(_button_label(text), data, style=style)

log = logging.getLogger("NextLevelVault")

class AccountManager:
    """
    Manages permanently-connected Telethon clients for each phone number.
    When Telegram sends an OTP (from user 777000), it's instantly forwarded to the buyer.
    """

    def __init__(self, accounts_col, bot_client, api_id, api_hash, pending_otp_requests):
        self.accounts_col = accounts_col
        self.bot = bot_client
        self.api_id = api_id
        self.api_hash = api_hash
        self.clients: Dict[str, TelegramClient] = {}
        self.pending_requests = pending_otp_requests

    async def add_client(self, phone: str, session_str: str):
        if phone in self.clients:
            await self.remove_client(phone)

        client = TelegramClient(StringSession(session_str), self.api_id, self.api_hash)
        try:
            await client.connect()

            # A TCP connection alone does not mean the Telegram authorization is
            # still valid. Revoked/stale auth keys can connect and then immediately
            # die with AuthKeyUnregisteredError. Validate before registering it.
            if not await client.is_user_authorized():
                raise RuntimeError("Telegram session is no longer authorized")
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass
            raise

        self.clients[phone] = client

        @client.on(events.NewMessage(from_users=777000))
        async def otp_handler(event):
            text = event.message.message
            code_match = re.search(r'Login code[:\s]+(\d{4,7})', text, re.I)
            if not code_match:
                code_match = re.search(r'\b(\d{5,6})\b', text)
            if not code_match:
                return

            otp = code_match.group(1)

            # Always bind the OTP to the newest sale of this phone. This matters
            # when the same number was sold earlier and later re-added to stock.
            buyer_doc = await self.accounts_col.find_one(
                {"phone": phone, "status": "sold"},
                sort=[("sold_at", -1)]
            )
            if not buyer_doc:
                log.warning(f"[OTP] No sold account found for {phone}")
                return

            buyer_id = buyer_doc.get("buyer_id")
            if not buyer_id:
                return

            key = (buyer_id, phone)
            is_first_otp = not buyer_doc.get("first_otp_sent", False)

            # Same behavior as the main OTP panel:
            # - the FIRST OTP after purchase is delivered automatically;
            # - later OTPs are delivered only after the buyer taps Request New OTP.
            if not is_first_otp and key not in self.pending_requests:
                log.info(
                    f"[OTP] Suppressed unrequested OTP for {buyer_id} / {phone} "
                    "(no pending request)."
                )
                return

            # ── Build message with fancy heading + Bullet Points ─────
            msg = f"{_fancy('• 📞 ᴘʜᴏɴᴇ')}: `{phone}`\n{_fancy('• 📩 ᴏᴛᴘ')}: `{otp}`"
            twofa = buyer_doc.get("twofa_password")
            if twofa:
                msg += f"\n{_fancy('• 🔐 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ')}: `{twofa}`"
            msg += _fancy(
                "\n\n⚠️ ɴᴏᴛᴇ: ʀᴇ-ʀᴇǫᴜᴇsᴛ ʙᴜᴛᴛᴏɴ ᴡᴏʀᴋs ғᴏʀ 72 ʜᴏᴜʀs."
                " ᴀғᴛᴇʀ ᴛʜᴀᴛ, ʀᴇǫᴜᴇsᴛ ᴀ ɴᴇᴡ ɴᴜᴍʙᴇʀ."
            )

            buttons = [[
                color_btn("🔄 ʀᴇǫᴜᴇsᴛ ɴᴇᴡ ᴏᴛᴘ", f"resend_{phone}", "primary"),
                color_btn("🔓 ʟᴏɢᴏᴜᴛ ғʀᴏᴍ ʙᴏᴛ", f"logout_{phone}", "danger"),
            ]]

            delivered = False
            try:
                await self.bot.send_message(buyer_id, msg, buttons=buttons)
                delivered = True
                log.info(f"[OTP] Forwarded OTP for {phone} to buyer {buyer_id}")
            except Exception as e:
                log.error(f"[OTP] Failed to send OTP to {buyer_id}: {e}")

            if delivered and is_first_otp:
                await self.accounts_col.update_one(
                    {"_id": buyer_doc["_id"]},
                    {"$set": {"first_otp_sent": True}}
                )

            if delivered:
                self.pending_requests.pop(key, None)

        log.info(f"[AccountManager] ✅ Client started for {phone}")

    async def remove_client(self, phone: str):
        if phone in self.clients:
            try:
                await self.clients[phone].disconnect()
            except Exception:
                pass
            del self.clients[phone]

    async def logout_client(self, phone: str):
        await self.remove_client(phone)
        log.info(f"[AccountManager] Client for {phone} logged out.")

    async def stop_all(self):
        for c in self.clients.values():
            try:
                await c.disconnect()
            except Exception:
                pass
        self.clients.clear()

    async def ensure_client(self, phone: str) -> bool:
        """Ensure the phone has a connected *and authorized* Telethon client."""
        client = self.clients.get(phone)
        if client is not None:
            try:
                if not client.is_connected():
                    await client.connect()
                if await client.is_user_authorized():
                    return True
                log.warning(f"[ensure_client] Existing session for {phone} is not authorized.")
            except Exception as e:
                log.warning(f"[ensure_client] Existing client for {phone} is unusable: {e}")
            await self.remove_client(phone)

        # Re-used phone numbers can have old SOLD records in history. Always
        # prefer the current AVAILABLE record; after purchase prefer the newest
        # SOLD record instead of an older recycled session.
        acc = await self.accounts_col.find_one(
            {"phone": phone, "status": "available"},
            sort=[("added_at", -1)],
        )
        if not acc:
            acc = await self.accounts_col.find_one(
                {"phone": phone, "status": "sold"},
                sort=[("sold_at", -1)],
            )

        if not acc or not acc.get("session_string"):
            log.warning(f"[ensure_client] No usable saved session found for {phone}.")
            return False

        try:
            await self.add_client(phone, acc["session_string"])
            log.info(f"[ensure_client] Restored authorized client for {phone} from saved session.")
            return True
        except Exception as e:
            log.error(f"[ensure_client] Failed to restore authorized session for {phone}: {e}")
            return False

    async def load_all(self):
        """Load one best authorized session per phone.

        Recycled numbers may have older SOLD documents. Do not let an old session
        race with and overwrite the newly-added/current session during startup.
        """
        sold_cutoff = datetime.utcnow() - timedelta(hours=72)
        query = {
            "$or": [
                {"status": "available"},
                {"status": "sold", "sold_at": {"$gte": sold_cutoff}},
            ]
        }

        best_by_phone = {}
        async for acc in self.accounts_col.find(query):
            phone = acc.get("phone")
            if not phone or not acc.get("session_string"):
                continue

            current = best_by_phone.get(phone)
            if current is None:
                best_by_phone[phone] = acc
                continue

            # AVAILABLE always wins over historical SOLD. Otherwise keep the
            # newest record for that status.
            if acc.get("status") == "available" and current.get("status") != "available":
                best_by_phone[phone] = acc
                continue
            if acc.get("status") != current.get("status"):
                continue

            field = "added_at" if acc.get("status") == "available" else "sold_at"
            if (acc.get(field) or datetime.min) > (current.get(field) or datetime.min):
                best_by_phone[phone] = acc

        docs = list(best_by_phone.values())
        sem = asyncio.Semaphore(5)

        async def load_one(doc):
            async with sem:
                try:
                    await self.add_client(doc["phone"], doc["session_string"])
                except Exception as e:
                    log.error(
                        f"[AccountManager] Skipped unusable session for {doc.get('phone')}: {e}"
                    )

        await asyncio.gather(*[load_one(d) for d in docs])
        log.info(f"[AccountManager] Loaded {len(self.clients)} authorized clients.")

    async def request_otp(self, phone: str, _retry_budget: int = 300) -> bool:
        """
        Trigger a Telegram login code without touching the authorized stock session.

        IMPORTANT:
        auth.SendCodeRequest must not be sent through the already-authorized
        account client. Doing that can make Telegram restart the auth flow and
        invalidate/disconnect the stock session (AuthRestartError followed by
        AuthKeyUnregisteredError).

        The authorized client remains connected only to listen for Telegram 777000.
        A fresh, unauthenticated temporary client is used to request the login code.
        """
        if not await self.ensure_client(phone):
            log.warning(f"[request_otp] Authorized listener for {phone} is unavailable.")
            return False

        listener = self.clients.get(phone)
        if listener is None or not listener.is_connected():
            log.warning(f"[request_otp] Authorized listener for {phone} is disconnected.")
            return False

        try:
            if not await listener.is_user_authorized():
                log.warning(f"[request_otp] Listener session for {phone} is no longer authorized.")
                await self.remove_client(phone)
                return False
        except Exception as e:
            log.warning(f"[request_otp] Could not validate listener for {phone}: {e}")
            await self.remove_client(phone)
            return False

        # Telegram may occasionally return AuthRestartError for send-code.
        # Retry only with a brand-new temporary auth client; never retry through
        # the authorized listener session.
        attempts = 2
        for attempt in range(1, attempts + 1):
            temp = TelegramClient(StringSession(), self.api_id, self.api_hash)
            try:
                await temp.connect()
                await temp.send_code_request(phone)
                log.info(
                    f"[request_otp] OTP trigger sent for {phone} "
                    f"using isolated auth client (attempt {attempt}/{attempts})"
                )
                return True
            except FloodWaitError as e:
                if e.seconds > _retry_budget:
                    log.warning(
                        f"[request_otp] Flood wait {e.seconds}s for {phone} "
                        "exceeds retry budget — giving up."
                    )
                    return False
                log.warning(f"[request_otp] Flood wait {e.seconds}s for {phone}")
                await asyncio.sleep(e.seconds)
                return await self.request_otp(phone, _retry_budget - e.seconds)
            except Exception as e:
                error_name = type(e).__name__
                if error_name == "AuthRestartError" and attempt < attempts:
                    log.warning(
                        f"[request_otp] Telegram requested auth restart for {phone}; "
                        "retrying with a fresh isolated client."
                    )
                    continue
                log.error(
                    f"[request_otp] Failed to trigger OTP for {phone} "
                    f"with isolated auth client: {error_name}: {e}"
                )
                return False
            finally:
                try:
                    await temp.disconnect()
                except Exception:
                    pass

        return False
