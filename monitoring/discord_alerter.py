# monitoring/discord_alerter.py
import requests

from utils.config import CONFIG
from utils.logger import log


class DiscordAlerter:
    def __init__(self):
        self.config = CONFIG["monitoring"]["discord"]
        self.enabled = self.config["enabled"]
        if self.enabled:
            self.webhook_url = self.config["webhook_url"]  # For trades
            self.error_webhook_url = self.config.get("error_webhook_url")  # For errors
            self.nse_webhook_url = self.config.get("nse_webhook_url")
            log.info("Discord alerter initialized.")
            if self.error_webhook_url:
                log.info("Discord error webhook configured separately.")

    def send_message(self, message: str):
        """Sends a plain text message to the Discord channel."""
        if not self.enabled or not self.webhook_url:
            return

        try:
            payload = {"content": message}
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            log.debug(f"Discord alert sent: {message[:50]}...")
        except Exception as e:  # noqa: BLE001
            log.error(f"Failed to send Discord alert: {e}")

    def send_embed(
        self,
        title: str,
        description: str,
        color: int = 0x00FF00,
        fields: dict | None = None,
        webhook_url: str | None = None,
    ):
        """
        Sends a rich embed message (looks nicer for trade alerts).
        Color: 0x00ff00 (green) for buy, 0xff0000 (red) for sell, 0xffa500 (orange) for error.
        """
        if not self.enabled:
            return

        # Use provided webhook_url or fall back to default
        url_to_use = webhook_url or self.webhook_url
        if not url_to_use:
            return

        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": None,
        }
        if fields:
            embed["fields"] = [
                {"name": k, "value": str(v), "inline": True} for k, v in fields.items()
            ]

        payload = {"embeds": [embed]}
        try:
            response = requests.post(url_to_use, json=payload, timeout=10)
            response.raise_for_status()
            log.debug(f"Discord embed sent: {title}")
        except Exception as e:  # noqa: BLE001
            log.error(f"Failed to send Discord embed: {e}")

    def send_trade_alert(self, symbol: str, action: str, quantity: int, price: float):
        """Sends a formatted trade alert as an embed."""
        if not self.enabled:
            return

        color = 0x00FF00 if action.upper() == "BUY" else 0xFF0000
        fields = {
            "Symbol": symbol,
            "Action": action.upper(),
            "Quantity": quantity,
            "Price": f"${price:.2f}",
        }
        self.send_embed(
            title="🚨 Trade Executed",
            description=f"{action.upper()} order filled.",
            color=color,
            fields=fields,
            webhook_url=self.webhook_url,  # Use trades webhook
        )

    def send_error_alert(self, error_message: str):
        """Sends an error alert as an embed."""
        if not self.enabled:
            return
        # Use error webhook if configured, otherwise fall back to main webhook
        error_webhook = self.error_webhook_url or self.webhook_url
        self.send_embed(title="⚠️ Bot Error", description=error_message, color=0xFFA500, webhook_url=error_webhook)

    def send_nse_report(self, report: str) -> None:
        """Send an NSE report to the NSE Discord webhook."""
        if not self.enabled or not self.nse_webhook_url:
            return
        try:
            payload = {"content": report}
            response = requests.post(self.nse_webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            log.debug(f"NSE report sent to Discord: {report[:50]}...")
        except Exception as e:  # noqa: BLE001
            log.error(f"Failed to send NSE report to Discord: {e}")
