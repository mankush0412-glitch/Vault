import asyncio
import logging
import re
from typing import Dict, Optional, List
from datetime import datetime

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.auth import SendCodeRequest
from telethon.tl.types import CodeSettings
from telethon.errors import (
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
)

log = logging.getLogger("NextLevelVault")

class AccountManager:
    def __init__(self, accounts_col, bot, api_id, api_hash, pending_otp_requests):
        self.accounts_col = accounts_col
        self.bot = bot
        self.api_id = api_id
        self.api_hash = api_hash
        self.pending_otp_requests = pending_otp_requests
        self.clients: Dict[str, TelegramClient] = {}

    async def load_all(self):
        """Load all available and sold sessions with parallel connection."""
        docs = []
        async for doc in self.accounts_col.find({"status": {"$in": ["available", "sold"]}}):
            if doc.get("phone") and doc.get("session_string"):
                docs.append(doc)
        sem = asyncio.Semaphore(5)  # Max 5 parallel connections
        async def connect_one(doc):
            async with sem:
                await self.add_client(doc["phone"], doc["session_string"], silent=True)
        await asyncio.gather(*[connect_one(d) for d in docs])
        log.info(f"[AccountManager] Loaded {len(self.clients)} clients.")

    async def add_client(self, phone: str, session_string: str, silent: bool = False):
        if phone in self.clients:
            return
        try:
            client = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
            await client.connect()
            self.clients[phone] = client
            log.info(f"[{phone}] ✅ Client loaded.")
            asyncio.create_task(self._listen_otp(phone, client))
        except Exception as e:
            log.error(f"[{phone}] Failed to add client: {e}")

    async def get_client(self, phone: str) -> Optional[TelegramClient]:
        return self.clients.get(phone)

    async def request_otp(self, phone: str, call: bool = False) -> bool:
        """Request OTP via SMS (call=False)"""
        client = self.clients.get(phone)
        if not client:
            doc = await self.accounts_col.find_one({"phone": phone})
            if doc and doc.get("session_string"):
                await self.add_client(phone, doc["session_string"])
                client = self.clients.get(phone)
            if not client:
                log.error(f"[{phone}] Client not available.")
                return False
        try:
            await client.request_code(phone, force_sms=True)
            log.info(f"[{phone}] ✅ OTP requested.")
            return True
        except FloodWaitError as e:
            log.warning(f"[{phone}] Flood wait: {e.seconds}s")
            await asyncio.sleep(e.seconds)
            return await self.request_otp(phone, call)
        except Exception as e:
            log.error(f"[{phone}] Failed to request OTP: {e}")
            return False

    async def logout_client(self, phone: str):
        client = self.clients.pop(phone, None)
        if client:
            try:
                await client.log_out()
                await client.disconnect()
            except Exception as e:
                log.warning(f"[{phone}] Logout error: {e}")

    async def _listen_otp(self, phone: str, client: TelegramClient):
        try:
            @client.on(events.NewMessage(incoming=True))
            async def handler(event):
                if event.is_private and event.message and event.message.text:
                    text = event.message.text
                    codes = re.findall(r'\b(\d{4,7})\b', text)
                    if codes:
                        otp_code = codes[0]
                        if (self.pending_otp_requests.get((None, phone)) or 
                            any(p == phone for (_, p) in self.pending_otp_requests.items())):
                            order = await self.accounts_col.find_one({"phone": phone, "status": "sold"}, sort=[("sold_at", -1)])
                            if order:
                                buyer_id = order.get("buyer_id")
                                if buyer_id:
                                    twofa = order.get("twofa_password", "N/A")
                                    await self.bot.send_message(
                                        buyer_id,
                                        f"📩 **OTP Received!**\n\n`{otp_code}`\n\n_This code is valid for a short time._\n2FA Password: `{twofa}`"
                                    )
                                    log.info(f"[{phone}] OTP forwarded to {buyer_id}")
                                    keys_to_remove = [k for k, v in self.pending_otp_requests.items() if v == phone]
                                    for k in keys_to_remove:
                                        self.pending_otp_requests.pop(k, None)
        except Exception as e:
            log.error(f"[{phone}] Listener error: {e}")
        await client.run_until_disconnected()
