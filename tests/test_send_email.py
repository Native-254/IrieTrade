# test_send_email.py
import os
from dotenv import load_dotenv
from monitoring.email_alerter import EmailAlerter

load_dotenv(override=True)

# Debug all email env vars
print("DEBUG: EMAIL_SENDER:", os.getenv('EMAIL_SENDER'))
print("DEBUG: EMAIL_BREVO_API_KEY:", "present" if os.getenv('EMAIL_BREVO_API_KEY') else "MISSING")
print("DEBUG: EMAIL_RECIPIENT:", os.getenv('EMAIL_RECIPIENT'))

alerter = EmailAlerter()
print("Alerter enabled:", alerter.enabled)

alerter.send_trade_alert("AAPL", "BUY", 100, 150.75)
print("Test trade alert sent. Check your inbox.")
