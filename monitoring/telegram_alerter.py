# monitoring/telegram_alerter.py
import requests

from utils.config import CONFIG
from utils.logger import log


class TelegramAlerter:
    def __init__(self):
        self.config = CONFIG["monitoring"]["telegram"]
        self.enabled = self.config["enabled"]
        if self.enabled:
            self.base_url = f"https://api.telegram.org/bot{self.config['bot_token']}/"
            log.info("Telegram alerter initialized.")

    def send_message(self, message: str):
        """Sends a message to the configured Telegram chat."""
        if not self.enabled:
            return

        try:
            url = self.base_url + "sendMessage"
            payload = {
                "chat_id": self.config["chat_id"],
                "text": message,
                "parse_mode": "HTML",
            }
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            log.debug(f"Telegram alert sent: {message[:50]}...")
        except Exception as e:  # noqa: BLE001
            log.error(f"Failed to send Telegram alert: {e}")

    def send_trade_alert(self, symbol: str, action: str, quantity: int, price: float):
        """Sends a formatted trade alert."""
        if not self.enabled:
            return
        msg = f"<b>Trade Executed</b>\nSymbol: {symbol}\nAction: {action}\nQuantity: {quantity}\nPrice: {price:.2f}"
        self.send_message(msg)

    def send_error_alert(self, error_message: str):
        """Sends a formatted error alert."""
        if not self.enabled:
            return
        msg = f"<b>⚠️ Bot Error</b>\n{error_message}"
        self.send_message(msg)
