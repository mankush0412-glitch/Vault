import re
import logging
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO)


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
        self.clients = {}                  # phone → TelegramClient
        self.pending_requests = pending_otp_requests  # (buyer_id, phone) → True

    # ------------------------------------------------------------------ #
    #  Add / remove / reload                                               #
    # ------------------------------------------------------------------ #
    async def add_client(self, phone: str, session_str: str):
        if phone in self.clients:
            await self.remove_client(phone)

        client = TelegramClient(StringSession(session_str), self.api_id, self.api_hash)
        # Use connect() instead of start() so we never trigger the interactive
        # login flow.  start() internally calls is_user_authorized() and, if it
        # returns False, tries to sign in — which breaks for sessions created with
        # a different API_ID even though the auth_key is valid.
        await client.connect()
        self.clients[phone] = client

        # ---- OTP listener ----
        @client.on(events.NewMessage(from_users=777000))
        async def otp_handler(event):
            text = event.message.message

            # Try different OTP patterns
            code_match = re.search(r'\b(\d{5,6})\b', text)
            if not code_match:
                code_match = re.search(r'Login code[:\s]+(\d+)', text, re.I)
            if not code_match:
                return  # not an OTP message

            otp = code_match.group(1)

            # Always look for the most recent buyer of this number
            buyer_doc = await self.accounts_col.find_one(
                {"phone": phone, "status": "sold"},
                sort=[("sold_at", -1)]
            )
            if not buyer_doc:
                return

            buyer_id = buyer_doc.get("buyer_id")
            if not buyer_id:
                return

            # Build message
            msg = f"📞 **Phone:** `{phone}`\n📩 **OTP:** `{otp}`"
            twofa = buyer_doc.get("twofa_password")
            if twofa:
                msg += f"\n🔐 **2FA Password:** `{twofa}`"
            msg += (
                "\n\n⚠️ **Note:** Re-Request button works for 72 hours."
                " After that, request a new number."
            )

            buttons = [[
                Button.inline("🔄 Request New OTP", f"resend_{phone}".encode()),
                Button.inline("🔓 Logout from Bot", f"logout_{phone}".encode()),
            ]]

            try:
                await self.bot.send_message(buyer_id, msg, buttons=buttons)
            except Exception as e:
                logging.error(f"[AccountManager] Failed to send OTP to {buyer_id}: {e}")

            # Clear pending request if any
            key = (buyer_id, phone)
            if key in self.pending_requests:
                del self.pending_requests[key]
                logging.info(f"[AccountManager] Cleared pending OTP for {buyer_id}/{phone}")

        logging.info(f"[AccountManager] ✅ Client started for {phone}")

    async def remove_client(self, phone: str):
        if phone in self.clients:
            try:
                await self.clients[phone].disconnect()
            except Exception:
                pass
            del self.clients[phone]

    async def logout_client(self, phone: str):
        """Called when buyer clicks 'Logout from Bot' — disconnects and removes."""
        await self.remove_client(phone)
        logging.info(f"[AccountManager] Client for {phone} logged out by buyer.")

    async def stop_all(self):
        for c in self.clients.values():
            try:
                await c.disconnect()
            except Exception:
                pass
        self.clients.clear()

    async def load_all(self):
        """Load all available accounts on startup."""
        async for acc in self.accounts_col.find({"status": "available"}):
            try:
                await self.add_client(acc["phone"], acc["session_string"])
            except Exception as e:
                logging.error(f"[AccountManager] Failed to load {acc.get('phone')}: {e}")

    # ------------------------------------------------------------------ #
    #  Trigger OTP manually (re-request)                                   #
    # ------------------------------------------------------------------ #
    async def request_otp(self, phone: str) -> bool:
        """
        Ask Telegram to resend the login code by calling SendCodeRequest.
        Returns True if triggered, False otherwise.
        """
        if phone not in self.clients:
            return False
        try:
            from telethon.tl.functions.auth import SendCodeRequest
            from telethon.tl.types import CodeSettings
            client = self.clients[phone]
            await client(SendCodeRequest(phone_number=phone, api_id=self.api_id,
                                          api_hash=self.api_hash, settings=CodeSettings()))
            return True
        except Exception as e:
            logging.error(f"[AccountManager] request_otp failed for {phone}: {e}")
            return False
