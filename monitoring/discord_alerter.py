# monitoring/discord_alerter.py
import requests
from typing import Optional
from utils.config import CONFIG
from utils.logger import log

class DiscordAlerter:
    def __init__(self):
        self.config = CONFIG['monitoring']['discord']
        self.enabled = self.config['enabled']
        if self.enabled:
            self.webhook_url = self.config['webhook_url']
            log.info("Discord alerter initialized.")

    def send_message(self, message: str):
        """Sends a plain text message to the Discord channel."""
        if not self.enabled or not self.webhook_url:
            return

        try:
            payload = {'content': message}
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            log.debug(f"Discord alert sent: {message[:50]}...")
        except Exception as e:
            log.error(f"Failed to send Discord alert: {e}")

    def send_embed(self, title: str, description: str, color: int = 0x00ff00, fields: Optional[dict] = None):
        """
        Sends a rich embed message (looks nicer for trade alerts).
        Color: 0x00ff00 (green) for buy, 0xff0000 (red) for sell, 0xffa500 (orange) for error.
        """
        if not self.enabled or not self.webhook_url:
            return

        embed = {
            'title': title,
            'description': description,
            'color': color,
            'timestamp': None  # You can add ISO timestamp if desired
        }
        if fields:
            embed['fields'] = [{'name': k, 'value': str(v), 'inline': True} for k, v in fields.items()]

        payload = {'embeds': [embed]}
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            log.debug(f"Discord embed sent: {title}")
        except Exception as e:
            log.error(f"Failed to send Discord embed: {e}")

    def send_trade_alert(self, symbol: str, action: str, quantity: int, price: float):
        """Sends a formatted trade alert as an embed."""
        if not self.enabled:
            return

        color = 0x00ff00 if action.upper() == 'BUY' else 0xff0000
        fields = {
            'Symbol': symbol,
            'Action': action.upper(),
            'Quantity': quantity,
            'Price': f"${price:.2f}"
        }
        self.send_embed(
            title="🚨 Trade Executed",
            description=f"{action.upper()} order filled.",
            color=color,
            fields=fields
        )

    def send_error_alert(self, error_message: str):
        """Sends an error alert as an embed."""
        if not self.enabled:
            return
        self.send_embed(
            title="⚠️ Bot Error",
            description=error_message,
            color=0xffa500
        )