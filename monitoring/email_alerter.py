import json
import os
import smtplib
import ssl
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

from utils.logger import log


class EmailAlerter:
    # Categories for threading
    CATEGORY_TRADES = "trades"
    CATEGORY_ERRORS = "errors"
    CATEGORY_AFRICA = "africa"

    # Static subjects — never change these
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
        self.smtp_enabled = all(
            [self.smtp_user, self.smtp_password, self.sender, self.recipient]
        )

        # Persistent thread state — stores the last Message-ID per category
        self._state_path = Path("data/email_thread_state.json")
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_state: dict = self._load_state()

        if self.brevo_enabled:
            log.info("Email alerter initialized (Brevo primary, SMTP fallback).")
        elif self.smtp_enabled:
            log.info("Email alerter initialized (SMTP only).")
        else:
            log.warning("Email alerter disabled — no credentials configured.")

    # ------------------------------------------------------------------
    # State persistence — survives restarts
    # ------------------------------------------------------------------
    def _load_state(self) -> dict:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text())
            except Exception as e:  # noqa: BLE001
                log.debug(f"Could not load email thread state: {e}")
                return {}
        return {}

    def _save_state(self) -> None:
        try:
            self._state_path.write_text(json.dumps(self._thread_state, indent=2))
        except Exception as e:  # noqa: BLE001
            log.debug(f"Could not persist email thread state: {e}")

    def _get_last_message_id(self, category: str) -> str | None:
        return self._thread_state.get(category)

    def _set_last_message_id(self, category: str, message_id: str) -> None:
        self._thread_state[category] = message_id
        self._save_state()

    # ------------------------------------------------------------------
    # Header construction
    # ------------------------------------------------------------------
    def _build_headers(self, category: str) -> dict[str, str]:
        """Build threading headers using the last known Message-ID for this category."""
        headers: dict[str, str] = {
            "X-Auto-Response-Suppress": "All",
            "Auto-Submitted": "auto-generated",
        }

        last_id = self._get_last_message_id(category)
        if last_id:
            headers["In-Reply-To"] = f"<{last_id}>"
            headers["References"] = f"<{last_id}>"

        # Do NOT include List-Unsubscribe — it pushes emails to Promotions
        return headers

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _send_via_brevo(
        self, subject: str, body_html: str, headers: dict[str, str]
    ) -> str | None:
        """Send via Brevo. Returns Brevo's Message-ID on success, None on failure."""
        sender = self.sender or ""
        recipient = self.recipient or ""
        if not (sender and recipient and self.api_key):
            return None

        url = "https://api.brevo.com/v3/smtp/email"
        payload = {
            "sender": {"email": sender, "name": "IrieTrade"},
            "to": [{"email": recipient}],
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
                data = resp.json()
                message_id = str(data.get("messageId", ""))
                log.info(f"Brevo sent '{subject}' — Message-ID: {message_id}")
                return message_id
            log.error(f"Brevo failed: {resp.status_code} {resp.text[:200]}")
            return None
        except Exception as e:  # noqa: BLE001
            log.error(f"Brevo request failed: {e}")
            return None

    def _send_via_smtp(
        self, subject: str, body_html: str, headers: dict[str, str]
    ) -> str | None:
        """Send via SMTP. Returns the SMTP Message-ID on success."""
        smtp_user = self.smtp_user or ""
        smtp_password = self.smtp_password or ""
        sender = self.sender or ""
        recipient = self.recipient or ""

        if not (smtp_user and smtp_password and sender and recipient):
            log.warning("SMTP send skipped — missing user/password/sender/recipient.")
            return None

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"IrieTrade <{sender}>"
        msg["To"] = recipient
        for k, v in headers.items():
            msg[k] = v

        msg_id = f"{uuid.uuid4()}@irietrade.me"
        msg["Message-ID"] = f"<{msg_id}>"

        msg.attach(MIMEText(body_html, "html"))
        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20) as server:
                server.starttls(context=ctx)
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
            return msg_id
        except Exception as e:  # noqa: BLE001
            log.error(f"SMTP failed: {e}")
            return None

    def send_message(
        self, subject: str, body_html: str, category: str
    ) -> None:
        """Send with Brevo, fall back to SMTP. Updates thread state on success."""
        if not self.recipient or not self.sender:
            log.warning("Email not sent — sender/recipient missing.")
            return

        headers = self._build_headers(category)

        if self.brevo_enabled:
            message_id = self._send_via_brevo(subject, body_html, headers)
            if message_id:
                self._set_last_message_id(category, message_id)
                return

        if self.smtp_enabled:
            log.warning("Brevo unavailable — falling back to SMTP")
            message_id = self._send_via_smtp(subject, body_html, headers)
            if message_id:
                self._set_last_message_id(category, message_id)
                return

        log.error(f"Email delivery failed for: {subject}")

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
    # Category-specific senders
    # ------------------------------------------------------------------
    def send_trade_alert(
        self, symbol: str, action: str, quantity: float, price: float,
        source: str = "general",
    ) -> None:
        action_color = "#00b894" if action in ("BUY", "BUY_TO_COVER") else "#e17055"

        def row(k: str, v: str) -> str:
            return (
                f'<tr>'
                f'<td style="padding:8px 12px;border-bottom:1px solid #2d3436;color:#8395a7;">{k}</td>'
                f'<td style="padding:8px 12px;border-bottom:1px solid #2d3436;">{v}</td>'
                f'</tr>'
            )

        inner = f"""
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
            {row("Symbol", symbol)}
            {row("Action", f'<span style="color:{action_color};font-weight:600;">{action}</span>')}
            {row("Quantity", str(quantity))}
            {row("Price", f"${price:,.4f}")}
            {row("Broker", source.upper())}
        </table>
        """
        body = self._shell("Trade Executed", action_color, inner)
        self.send_message(self.SUBJECT_TRADES, body, self.CATEGORY_TRADES)

    def send_error_alert(self, error_msg: str, source: str = "general") -> None:
        inner = (
            f'<p style="background:#1e1e2f;padding:14px;border-left:4px solid #e17055;'
            f'font-family:monospace;font-size:13px;line-height:1.5;color:#dfe6e9;">'
            f'{error_msg}</p>'
        )
        body = self._shell(f"Error — {source.upper()}", "#e17055", inner)
        self.send_message(self.SUBJECT_ERRORS, body, self.CATEGORY_ERRORS)

    def send_africa_brief(self, briefs: dict) -> None:
        """briefs: {"NSE": {"report": str, "insight": str}, ...}"""
        sections = []
        for exchange, payload in briefs.items():
            report = payload.get("report", "")
            insight = payload.get("insight", "")
            insight_html = (
                f'<p style="margin:0;padding-top:10px;border-top:1px solid #2d3436;'
                f'font-size:13px;line-height:1.6;color:#b2bec3;">'
                f'<strong>Insight:</strong> {insight}</p>'
                if insight else ""
            )
            section = f"""
            <div style="background:#151823;border:1px solid #2d3436;border-radius:10px;
                        padding:16px;margin-bottom:14px;">
                <h3 style="color:#00cec9;margin:0 0 10px;font-size:15px;letter-spacing:.03em;">
                    {exchange}
                </h3>
                <pre style="font-family:monospace;font-size:13px;line-height:1.5;
                            white-space:pre-wrap;margin:0 0 10px;color:#dfe6e9;">{report}</pre>
                {insight_html}
            </div>
            """
            sections.append(section)

        inner = "".join(sections)
        body = self._shell("African Market Briefs", "#00cec9", inner)
        self.send_message(self.SUBJECT_AFRICA, body, self.CATEGORY_AFRICA)