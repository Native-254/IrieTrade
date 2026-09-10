# monitoring/telegram_alerter.py
import requests

from utils.config import CONFIG
from utils.logger import log


class TelegramAlerter:
    def __init__(self):
        self.config = CONFIG["monitoring"]["telegram"]
        self.bot_token = self.config.get("bot_token", "")
        self.chat_id = self.config.get("chat_id", "")
        self.group_id = self.config.get("group_id", "")
        self.channel_id = self.config.get("channel_id", "")
        self.enabled = bool(self.bot_token and self.chat_id)
        if self.enabled:
            self.base_url = f"https://api.telegram.org/bot{self.bot_token}/"
            log.info("Telegram alerter initialized.")

    def _send(self, chat_id: str, text: str) -> None:
        if not (self.bot_token and chat_id):
            return

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            response = requests.post(url, data=payload, timeout=10)
            if response.status_code != 200:
                log.error(f"Telegram send failed: {response.status_code} {response.text}")
        except Exception as e:  # noqa: BLE001
            log.error(f"Telegram send exception: {e}")

    def send_message(self, message: str):
        """Sends a message to the configured Telegram chat."""
        self._send(self.chat_id, message)

    def send_trade_alert(self, symbol: str, action: str, quantity: int, price: float):
        """Sends a formatted trade alert."""
        if not self.enabled:
            return
        msg = f"<b>Trade Executed</b>\nSymbol: {symbol}\nAction: {action}\nQuantity: {quantity}\nPrice: {price:.2f}"
        self.send_message(msg)

    def send_channel_signal(self, text: str) -> None:
        """Posts a signal to the configured Telegram channel."""
        self._send(self.channel_id, text)

    def send_group_message(self, text: str) -> None:
        """Posts a note to the configured Telegram discussion group."""
        self._send(self.group_id, text)

    def send_error_alert(self, error_message: str):
        """Sends a formatted error alert."""
        if not self.enabled:
            return
        msg = f"<b>⚠️ Bot Error</b>\n{error_message}"
        self.send_message(msg)
