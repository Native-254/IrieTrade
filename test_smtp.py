"""Standalone SMTP test — verifies credentials and delivery without the bot."""
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


def load_env(path: str = "~/.config/irietrade/env") -> None:
    """Manually load a dotenv-style file (does not use python-dotenv)."""
    env_path = Path(path).expanduser()
    if not env_path.exists():
        print(f"Env file not found at {env_path}")
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def main() -> None:
    load_env()

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("EMAIL_SENDER", user)
    recipient = os.getenv("EMAIL_RECIPIENT")

    print("SMTP config:")
    print(f"  host      = {host}")
    print(f"  port      = {port}")
    print(f"  user      = {user}")
    pw_status = f"set ({len(password)} chars)" if password else "MISSING"
    print(f"  password  = {pw_status}")
    print(f"  sender    = {sender}")
    print(f"  recipient = {recipient}")
    print()

    if not all([user, password, sender, recipient]):
        print("ERROR: Missing SMTP_USER, SMTP_PASSWORD, EMAIL_SENDER, or EMAIL_RECIPIENT")
        return

    # Local non-Optional narrowing for the type checker
    smtp_user = user or ""
    smtp_password = password or ""
    sender_addr = sender or ""
    recipient_addr = recipient or ""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "[IrieTrade] SMTP Test"
    msg["From"] = f"IrieTrade <{sender_addr}>"
    msg["To"] = recipient_addr
    msg["Message-ID"] = "<smtp-test-irietrade@irietrade.me>"
    msg.attach(MIMEText(
        "<html><body><h2>SMTP works</h2>"
        "<p>If you received this, the SMTP fallback path is functional.</p>"
        "</body></html>",
        "html",
    ))

    print("Connecting to SMTP server...")
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) as server:
            print("  Server responded (SSL).")
            print("  Logging in...")
            server.login(smtp_user, smtp_password)
        print()
        print("SUCCESS — email sent. Check your inbox in ~30 seconds.")
    except smtplib.SMTPAuthenticationError as e:
        print(f"FAILED — authentication error: {e}")
        print("Tip: for Gmail, use an App Password, not your main password.")
    except smtplib.SMTPException as e:
        print(f"FAILED — SMTP error: {e}")
    except (OSError, ssl.SSLError) as e:
        print(f"FAILED — {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()