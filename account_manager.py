import re
import asyncio
import logging
from typing import Dict, Optional
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError

# ─── Fancy text (small caps) ──────────────────────────────────────
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
        await client.connect()
        self.clients[phone] = client

        @client.on(events.NewMessage(from_users=777000))
        async def otp_handler(event):
            text = event.message.message
            code_match = re.search(r'\b(\d{5,6})\b', text)
            if not code_match:
                code_match = re.search(r'Login code[:\s]+(\d+)', text, re.I)
            if not code_match:
                return

            otp = code_match.group(1)

            # Check if this phone is pending for any buyer
            pending_phones = [k[1] for k in self.pending_requests.keys()]
            if phone not in pending_phones:
                log.info(f"[OTP] Received OTP for {phone} but no pending request – ignoring.")
                return

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

            # ── Build message with fancy heading ──────────────────
            msg = f"{_fancy('📞 ᴘʜᴏɴᴇ')}: `{phone}`\n{_fancy('📩 ᴏᴛᴘ')}: `{otp}`"
            twofa = buyer_doc.get("twofa_password")
            if twofa:
                msg += f"\n{_fancy('🔐 2ꜰᴀ ᴘᴀssᴡᴏʀᴅ')}: `{twofa}`"
            msg += _fancy(
                "\n\n⚠️ ɴᴏᴛᴇ: ʀᴇ-ʀᴇǫᴜᴇsᴛ ʙᴜᴛᴛᴏɴ ᴡᴏʀᴋs ғᴏʀ 72 ʜᴏᴜʀs."
                " ᴀғᴛᴇʀ ᴛʜᴀᴛ, ʀᴇǫᴜᴇsᴛ ᴀ ɴᴇᴡ ɴᴜᴍʙᴇʀ."
            )

            buttons = [[
                Button.inline("🔄 ʀᴇǫᴜᴇsᴛ ɴᴇᴡ ᴏᴛᴘ", f"resend_{phone}".encode()),
                Button.inline("🔓 ʟᴏɢᴏᴜᴛ ғʀᴏᴍ ʙᴏᴛ", f"logout_{phone}".encode()),
            ]]

            try:
                await self.bot.send_message(buyer_id, msg, buttons=buttons)
                log.info(f"[OTP] Forwarded OTP for {phone} to buyer {buyer_id}")
            except Exception as e:
                log.error(f"[OTP] Failed to send OTP to {buyer_id}: {e}")

            # Clear pending
            keys_to_remove = [k for k in self.pending_requests if k[1] == phone]
            for k in keys_to_remove:
                self.pending_requests.pop(k, None)

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

    async def load_all(self):
        """Load all available accounts with parallel connection (up to 5 at a time)."""
        docs = []
        async for acc in self.accounts_col.find({"status": "available"}):
            if acc.get("phone") and acc.get("session_string"):
                docs.append(acc)

        sem = asyncio.Semaphore(5)

        async def load_one(doc):
            async with sem:
                try:
                    await self.add_client(doc["phone"], doc["session_string"])
                except Exception as e:
                    log.error(f"[AccountManager] Failed to load {doc.get('phone')}: {e}")

        await asyncio.gather(*[load_one(d) for d in docs])
        log.info(f"[AccountManager] Loaded {len(self.clients)} clients.")

    async def request_otp(self, phone: str) -> bool:
        if phone not in self.clients:
            log.warning(f"[request_otp] Client for {phone} not available.")
            return False
        try:
            from telethon.tl.functions.auth import SendCodeRequest
            from telethon.tl.types import CodeSettings
            client = self.clients[phone]
            await client(SendCodeRequest(
                phone_number=phone,
                api_id=self.api_id,
                api_hash=self.api_hash,
                settings=CodeSettings()
            ))
            log.info(f"[request_otp] OTP request sent for {phone}")
            return True
        except FloodWaitError as e:
            log.warning(f"[request_otp] Flood wait {e.seconds}s for {phone}")
            await asyncio.sleep(e.seconds)
            return await self.request_otp(phone)
        except Exception as e:
            log.error(f"[request_otp] Failed for {phone}: {e}")
            return False
