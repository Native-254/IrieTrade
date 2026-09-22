import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
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

    # ------------------------------------------------------------------
    # Error budgeting and deduplication
    # ------------------------------------------------------------------
    # Errors at or above this severity are eligible for email
    _EMAIL_SEVERITIES = {"critical", "important"}  # noqa: RUF012

    # Default dedup window per normalized message (hours)
    _DEFAULT_DEDUP_HOURS = 6

    # Daily email budget (leave headroom under Resend's 100/day free tier)
    _DAILY_EMAIL_BUDGET = int(os.getenv("EMAIL_DAILY_BUDGET", "80"))

    def __init__(self):
        # Resend (primary) — sender must be at a verified domain
        self.resend_api_key = os.getenv("RESEND_API_KEY")
        self.resend_sender = os.getenv("RESEND_SENDER")
        self.resend_enabled = bool(self.resend_api_key and self.resend_sender)

        # Brevo (fallback) — sender must be pre-verified in Brevo
        self.brevo_api_key = os.getenv("EMAIL_BREVO_API_KEY")
        self.brevo_sender = os.getenv("BREVO_SENDER")
        self.brevo_enabled = bool(self.brevo_api_key and self.brevo_sender)

        # Recipient (shared)
        self.recipient = os.getenv("EMAIL_RECIPIENT")
        self.logo_url = os.getenv("EMAIL_LOGO_URL", "https://irietrade.me/logo.png")

        # Persistent thread state — stores the last Message-ID per category
        self._state_path = Path("data/email_thread_state.json")
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_state: dict = self._load_state()

        # Error state for dedup and budgeting
        self._error_state_path = Path("data/email_error_state.json")
        self._error_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._error_state: dict = self._load_error_state()

        providers = []
        if self.resend_enabled:
            providers.append("Resend")
        if self.brevo_enabled:
            providers.append("Brevo")
        if not self.recipient:
            log.warning("Email alerter disabled — EMAIL_RECIPIENT missing.")
        elif providers:
            log.info(f"Email alerter initialized ({' → '.join(providers)}).")
        else:
            log.warning("Email alerter disabled — no API keys configured.")

    # ------------------------------------------------------------------
    # State persistence
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
    # Error dedup and budgeting
    # ------------------------------------------------------------------
    def _load_error_state(self) -> dict:
        if self._error_state_path.exists():
            try:
                return json.loads(self._error_state_path.read_text())
            except Exception as e:  # noqa: BLE001
                log.debug(f"Could not load error state: {e}")
        return {"hashes": {}, "daily_count": 0, "daily_date": ""}

    def _save_error_state(self) -> None:
        try:
            self._error_state_path.write_text(
                json.dumps(self._error_state, indent=2)
            )
        except Exception as e:  # noqa: BLE001
            log.debug(f"Could not persist error state: {e}")

    @staticmethod
    def _normalize_error_key(msg: str) -> str:
        """Strip volatile bits (numbers, IDs, timestamps) so the same
        underlying error collapses to one key across occurrences."""
        s = msg.lower()
        # Remove timestamps like 2026-09-21 10:01:22
        s = re.sub(r"\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}:\d{2}", "<ts>", s)
        # Remove hex ids, order ids, request ids
        s = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", s)
        s = re.sub(r"\b(reqid|orderid|reqid=)\s*\d+", r"\1 <n>", s)
        # Remove any run of digits (prices, quantities, counts)
        s = re.sub(r"\d+\.?\d*", "<n>", s)
        # Collapse whitespace
        s = re.sub(r"\s+", " ", s).strip()
        # Truncate so identical prefixes dedup even if suffix differs
        return hashlib.sha256(s[:200].encode("utf-8")).hexdigest()[:16]

    def _error_budget_ok(self) -> bool:
        """Return True if we're still under today's email budget."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state = self._error_state
        if state.get("daily_date") != today:
            state["daily_date"] = today
            state["daily_count"] = 0
            self._save_error_state()
        return int(state.get("daily_count", 0)) < self._DAILY_EMAIL_BUDGET

    def _error_dedup_ok(
        self, msg: str, dedup_hours: int = _DEFAULT_DEDUP_HOURS
    ) -> bool:
        """Return True if this error hasn't been emailed recently."""
        key = self._normalize_error_key(msg)
        now = datetime.now(timezone.utc)
        hashes: dict = self._error_state.setdefault("hashes", {})
        last_iso = hashes.get(key)
        if last_iso:
            try:
                last = datetime.fromisoformat(last_iso)
                if now - last < timedelta(hours=dedup_hours):
                    log.debug(
                        f"Suppressing duplicate error email (key={key}, "
                        f"last sent {last_iso})"
                    )
                    return False
            except Exception:  # noqa: BLE001, S110
                pass
        hashes[key] = now.isoformat()
        # Keep the hash table small — prune anything older than 7 days
        cutoff = now - timedelta(days=7)
        for k in list(hashes.keys()):
            try:
                if datetime.fromisoformat(hashes[k]) < cutoff:
                    del hashes[k]
            except Exception:  # noqa: BLE001
                del hashes[k]
        self._save_error_state()
        return True

    def _record_email_sent(self) -> None:
        state = self._error_state
        state["daily_count"] = int(state.get("daily_count", 0)) + 1
        self._save_error_state()

    # ------------------------------------------------------------------
    # Header construction
    # ------------------------------------------------------------------
    def _build_headers(self, category: str) -> dict[str, str]:
        """Threading headers based on the last known Message-ID for this category."""
        headers: dict[str, str] = {
            "X-Auto-Response-Suppress": "All",
            "Auto-Submitted": "auto-generated",
        }

        last_id = self._get_last_message_id(category)
        if last_id:
            headers["In-Reply-To"] = f"<{last_id}>"
            headers["References"] = f"<{last_id}>"

        return headers

    # ------------------------------------------------------------------
    # Resend transport (primary)
    # ------------------------------------------------------------------
    def _send_via_resend(
        self, subject: str, body_html: str, headers: dict[str, str]
    ) -> str | None:
        sender = self.resend_sender or ""
        recipient = self.recipient or ""
        if not (sender and recipient and self.resend_api_key):
            return None

        url = "https://api.resend.com/emails"
        payload = {
            "from": f"IrieTrade <{sender}>",
            "to": [recipient],
            "subject": subject,
            "html": body_html,
            "headers": headers,
        }
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.resend_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                message_id = str(data.get("id", ""))
                log.info(f"Resend sent '{subject}' — ID: {message_id}")
                return message_id
            log.error(f"Resend failed: {resp.status_code} {resp.text[:200]}")
            return None
        except Exception as e:  # noqa: BLE001
            log.error(f"Resend request failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Brevo transport (fallback)
    # ------------------------------------------------------------------
    def _send_via_brevo(
        self, subject: str, body_html: str, headers: dict[str, str]
    ) -> str | None:
        sender = self.brevo_sender or ""
        recipient = self.recipient or ""
        if not (sender and recipient and self.brevo_api_key):
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
                headers={
                    "api-key": self.brevo_api_key,
                    "Content-Type": "application/json",
                },
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

    # ------------------------------------------------------------------
    # Dispatch — Resend primary, Brevo fallback
    # ------------------------------------------------------------------
    def send_message(self, subject: str, body_html: str, category: str) -> None:
        if not self.recipient:
            log.warning("Email not sent — EMAIL_RECIPIENT missing.")
            return

        headers = self._build_headers(category)

        # Primary: Resend
        if self.resend_enabled:
            message_id = self._send_via_resend(subject, body_html, headers)
            if message_id:
                self._set_last_message_id(category, message_id)
                return

        # Fallback: Brevo
        if self.brevo_enabled:
            log.warning("Resend unavailable — falling back to Brevo")
            message_id = self._send_via_brevo(subject, body_html, headers)
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

    def send_error_alert(
        self,
        error_msg: str,
        source: str = "general",
        severity: str = "important",
        dedup_hours: int = _DEFAULT_DEDUP_HOURS,
    ) -> None:
        """Send an error email, subject to severity + dedup + budget rules.

        severity:
          - "critical" → always emails (still subject to daily budget)
          - "important" (default) → emails once per dedup window
          - "info" / "warning" → never emails, only logs
        """
        # Layer 1: severity filter
        if severity not in self._EMAIL_SEVERITIES:
            log.debug(
                f"[email-suppressed:{severity}] {source}: {error_msg}"
            )
            return

        # Layer 2: deduplication
        effective_dedup = 0 if severity == "critical" else dedup_hours
        if effective_dedup > 0 and not self._error_dedup_ok(
            error_msg, effective_dedup
        ):
            return

        # Layer 3: daily budget
        if not self._error_budget_ok():
            log.warning(
                f"Email budget exhausted for today "
                f"({self._DAILY_EMAIL_BUDGET} max). Error logged but not emailed."
            )
            return

        inner = (
            f'<p style="background:#1e1e2f;padding:14px;border-left:4px solid #e17055;'
            f'font-family:monospace;font-size:13px;line-height:1.5;color:#dfe6e9;">'
            f'{error_msg}</p>'
        )
        body = self._shell(f"{severity.capitalize()} — {source.upper()}", "#e17055", inner)
        self.send_message(self.SUBJECT_ERRORS, body, self.CATEGORY_ERRORS)
        self._record_email_sent()

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