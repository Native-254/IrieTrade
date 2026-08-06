# tests/test_send_email.py
import os
from pathlib import Path

from dotenv import load_dotenv

from monitoring.email_alerter import EmailAlerter

root_dir = Path(__file__).resolve().parent.parent
load_dotenv(root_dir / ".env", override=True)

print("DEBUG: EMAIL_SENDER:", os.getenv("EMAIL_SENDER"))
print(
    "DEBUG: EMAIL_BREVO_API_KEY:",
    "present" if os.getenv("EMAIL_BREVO_API_KEY") else "MISSING",
)
print("DEBUG: EMAIL_RECIPIENT:", os.getenv("EMAIL_RECIPIENT"))

alerter = EmailAlerter()
print("Alerter enabled:", alerter.enabled)

if not alerter.enabled:
    raise SystemExit("Email settings are incomplete. Check your .env file.")

alerter.send_trade_alert("AAPL", "BUY", 100, 150.75)
print("Test trade alert sent. Check your inbox.")
