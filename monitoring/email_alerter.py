import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from utils.logger import log


class EmailAlerter:
    # Fixed thread roots — every message in a category references the same root,
    # which is what Gmail needs to collapse them into one conversation.
    ROOT_TRADES = "irietrade-trades-root@irietrade.me"
    ROOT_ERRORS = "irietrade-errors-root@irietrade.me"
    ROOT_AFRICA = "irietrade-africa-root@irietrade.me"

    # Static subjects — never change these, or Gmail will split the thread.
    SUBJECT_TRADES = "[IrieTrade] Trade Alerts"
    SUBJECT_ERRORS = "[IrieTrade] Errors"
    SUBJECT_AFRICA = "[IrieTrade] African Market Briefs"

    def __init__(self):
        # Brevo (primary)
        self.sender = os.getenv("EMAIL_SENDER")
        self.api_key = os.getenv("EMAIL_BREVO_API_KEY")
        self.recipient = os.getenv("EMAIL_RECIPIENT")
        self.logo_url = os.getenv("EMAIL_LOGO_URL", "https://irietrade.me/logo.png")
        self.brevo_enabled = all([self.sender, self.api_key, self.recipient])

        # SMTP (fallback)
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER")
        self.smtp_password = os.getenv("SMTP_PASSWORD")
        self.smtp_enabled = all([self.smtp_user, self.smtp_password, self.sender, self.recipient])

        if self.brevo_enabled:
            log.info("Email alerter initialized (Brevo primary, SMTP fallback).")
        elif self.smtp_enabled:
            log.info("Email alerter initialized (SMTP only).")
        else:
            log.warning("Email alerter disabled — no credentials configured.")

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _headers_for(self, root_id: str) -> dict:
        """Standard headers for threading + Gmail Updates-tab routing."""
        return {
            "In-Reply-To": f"<{root_id}>",
            "References": f"<{root_id}>",
            # Gmail routes bulk mail to Updates when these are present
            "Precedence": "bulk",
            "X-Auto-Response-Suppress": "All",
            "List-Id": "IrieTrade Alerts <alerts.irietrade.me>",
            "List-Unsubscribe": f"<mailto:{self.sender}?subject=unsubscribe>",
            "Auto-Submitted": "auto-generated",
        }

    def _send_via_brevo(self, subject: str, body_html: str, headers: dict) -> bool:
        url = "https://api.brevo.com/v3/smtp/email"
        payload = {
            "sender": {"email": self.sender, "name": "IrieTrade"},
            "to": [{"email": self.recipient}],
            "subject": subject,
            "htmlContent": body_html,
            "headers": headers,
        }
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code == 201:
                return True
            log.error(f"Brevo failed: {resp.status_code} {resp.text[:200]}")
            return False
        except Exception as e:  # noqa: BLE001
            log.error(f"Brevo request failed: {e}")
            return False

    def _send_via_smtp(self, subject: str, body_html: str, headers: dict) -> bool:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"IrieTrade <{self.sender}>"
        msg["To"] = self.recipient or ""
        for k, v in headers.items():
            msg[k] = v
        msg.attach(MIMEText(body_html, "html"))
        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20) as server:
                server.starttls(context=ctx)
                server.login(self.smtp_user or "", self.smtp_password or "")
                server.send_message(msg)
            return True
        except Exception as e:  # noqa: BLE001
            log.error(f"SMTP failed: {e}")
            return False

    def send_message(self, subject: str, body_html: str, root_id: str) -> None:
        """Send with Brevo, fall back to SMTP if Brevo fails or is unconfigured."""
        if not self.recipient or not self.sender:
            log.warning("Email not sent — sender/recipient missing.")
            return
        headers = self._headers_for(root_id)

        if self.brevo_enabled and self._send_via_brevo(subject, body_html, headers):
            log.info(f"Email sent via Brevo: {subject}")
            return

        if self.smtp_enabled:
            log.warning("Brevo unavailable — falling back to SMTP")
            if self._send_via_smtp(subject, body_html, headers):
                log.info(f"Email sent via SMTP: {subject}")
                return

        log.error(f"Email delivery failed for: {subject}")

    def send_email(self, subject: str, body: str) -> None:
        """Send a generic email (used for reports) without threading."""
        # Convert plain text to basic HTML
        body_html = f"<p>{body.replace(chr(10), '<br>')}</p>"
        # Use a dummy root ID since we don't want threading for generic emails
        dummy_root_id = "generic-email@irietrade.me"
        self.send_message(subject, body_html, dummy_root_id)

    # ------------------------------------------------------------------
    # Layout helpers
    # ------------------------------------------------------------------
    def _signature(self) -> str:
        return """
        <br><br>
        <hr style="border:0;border-top:1px solid #2d3436;margin:20px 0;">
        <p style="font-size:12px;color:#636e72;line-height:1.5;">
            From <strong>IrieTrade</strong> — algorithmic trading signals and market analysis.<br>
            <a href="https://irietrade.me" style="color:#00cec9;">irietrade.me</a>
        </p>
        """

    def _shell(self, title: str, title_color: str, inner_html: str) -> str:
        return f"""
        <html>
        <body style="font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;
                     background:#0b111a;color:#dfe6e9;padding:24px;margin:0;">
            <div style="max-width:640px;margin:0 auto;">
                <div style="text-align:center;margin-bottom:20px;">
                    <img src="{self.logo_url}" alt="IrieTrade"
                         style="max-width:180px;height:auto;">
                </div>
                <h2 style="color:{title_color};margin:0 0 18px;">{title}</h2>
                {inner_html}
                {self._signature()}
            </div>
        </body>
        </html>
        """

    # ------------------------------------------------------------------
    # Trade alerts
    # ------------------------------------------------------------------
    def send_trade_alert(self, symbol, action, quantity, price, source: str = "general"):
        action_color = "#00b894" if action in ("BUY", "BUY_TO_COVER") else "#e17055"
        row = lambda k, v: (
            f'<tr>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #2d3436;color:#8395a7;">{k}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #2d3436;">{v}</td>'
            f'</tr>'
        )
        inner = f"""
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
            {row("Symbol", symbol)}
            {row("Action", f'<span style="color:{action_color};font-weight:600;">{action}</span>')}
            {row("Quantity", quantity)}
            {row("Price", f"${price:,.4f}")}
            {row("Broker", source.upper())}
        </table>
        """
        body = self._shell("Trade Executed", action_color, inner)
        self.send_message(self.SUBJECT_TRADES, body, self.ROOT_TRADES)

    # ------------------------------------------------------------------
    # Error alerts
    # ------------------------------------------------------------------
    def send_error_alert(self, error_msg: str, source: str = "general"):
        inner = (
            f'<p style="background:#1e1e2f;padding:14px;border-left:4px solid #e17055;'
            f'font-family:monospace;font-size:13px;line-height:1.5;color:#dfe6e9;">'
            f'{error_msg}</p>'
        )
        body = self._shell(f"Error — {source.upper()}", "#e17055", inner)
        self.send_message(self.SUBJECT_ERRORS, body, self.ROOT_ERRORS)

    # ------------------------------------------------------------------
    # African briefs — one email, all exchanges, styled
    # ------------------------------------------------------------------
    def send_africa_brief(self, briefs: dict) -> None:
        """briefs: {"NSE": {"report": str, "insight": str}, "JSE": {...}, ...}"""
        sections = []
        for exchange, payload in briefs.items():
            report = payload.get("report", "")
            insight = payload.get("insight", "")
            section = f"""
            <div style="background:#151823;border:1px solid #2d3436;border-radius:10px;
                        padding:16px;margin-bottom:14px;">
                <h3 style="color:#00cec9;margin:0 0 10px;font-size:15px;letter-spacing:.03em;">
                    {exchange}
                </h3>
                <pre style="font-family:monospace;font-size:13px;line-height:1.5;
                            white-space:pre-wrap;margin:0 0 10px;color:#dfe6e9;">{report}</pre>
                {f'<p style="margin:0;padding-top:10px;border-top:1px solid #2d3436;'
                   f'font-size:13px;line-height:1.6;color:#b2bec3;"><strong>Insight:</strong> {insight}</p>'
                   if insight else ''}
            </div>
            """
            sections.append(section)

        inner = "".join(sections)
        body = self._shell("African Market Briefs", "#00cec9", inner)
        self.send_message(self.SUBJECT_AFRICA, body, self.ROOT_AFRICA)