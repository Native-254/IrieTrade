# monitoring/email_alerter.py
import os
import requests
from utils.logger import log

class EmailAlerter:
    def __init__(self):
        self.sender = os.getenv('EMAIL_SENDER')
        self.api_key = os.getenv('EMAIL_BREVO_API_KEY')
        self.recipient = os.getenv('EMAIL_RECIPIENT')
        self.logo_url = os.getenv('EMAIL_LOGO_URL', 'https://irie-web.vercel.app/logo.png')
        self.enabled = all([self.sender, self.api_key, self.recipient])
        if self.enabled:
            log.info("Email alerter initialized (Brevo API).")

    def send_message(self, subject: str, body_html: str):
        """Send an HTML email via Brevo API."""
        if not self.enabled:
            return

        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "sender": {"email": self.sender},
            "to": [{"email": self.recipient}],
            "subject": subject,
            "htmlContent": body_html
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code == 201:
                log.info(f"Email sent: {subject}")
            else:
                log.error(f"Brevo failed: {resp.status_code} {resp.text}")
        except Exception as e:
            log.error(f"Failed to send email via Brevo: {e}")

    def _build_signature(self) -> str:
        """Return the standard email footer."""
        return """
        <br><br>
        <hr style="border:1px solid #2d3436;">
        <p style="font-size:12px; color:#636e72;">
            From <strong>IrieTrade</strong> – your automated trading partner.<br>
            <a href="https://irie-web.vercel.app" style="color:#00cec9;">See our web!</a>
        </p>
        """

    def send_trade_alert(self, symbol: str, action: str, quantity: int, price: float):
        subject = f"IrieTrade Alert: {action} {quantity} {symbol} @ ${price:,.2f}"
        action_color = "#00b894" if action in ('BUY', 'BUY_TO_COVER') else "#e17055"
        body = f"""
        <html>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0b111a; color: #dfe6e9; padding: 20px;">
            <div style="text-align: center; margin-bottom: 20px;">
                <img src="{self.logo_url}" alt="IrieTrade Logo" style="max-width: 200px; height: auto;">
            </div>
            <h2 style="color: {action_color};">Trade Executed</h2>
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #2d3436;"><strong>Symbol</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #2d3436;">{symbol}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #2d3436;"><strong>Action</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #2d3436; color: {action_color};">{action}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #2d3436;"><strong>Quantity</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #2d3436;">{quantity}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #2d3436;"><strong>Price</strong></td>
                    <td style="padding: 10px; border-bottom: 1px solid #2d3436;">${price:,.2f}</td>
                </tr>
            </table>
            {self._build_signature()}
        </body>
        </html>
        """
        self.send_message(subject, body)

    def send_error_alert(self, error_msg: str):
        subject = "IrieTrade Error Alert"
        body = f"""
        <html>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0b111a; color: #dfe6e9; padding: 20px;">
            <div style="text-align: center; margin-bottom: 20px;">
                <img src="{self.logo_url}" alt="IrieTrade Logo" style="max-width: 200px; height: auto;">
            </div>
            <h2 style="color: #e17055;">⚠️ An Error Occurred</h2>
            <p style="background-color: #1e1e2f; padding: 15px; border-left: 4px solid #e17055; margin: 20px 0; font-family: monospace;">
                {error_msg}
            </p>
            {self._build_signature()}
        </body>
        </html>
        """
        self.send_message(subject, body)