"""Standalone Resend test — verifies the primary path."""
import os
from pathlib import Path

import requests


def load_env(path: str = "~/.config/irietrade/env") -> None:
    env_path = Path(path).expanduser()
    if not env_path.exists():
        print(f"Env file not found at {env_path}")
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def main() -> None:
    load_env()

    api_key = os.getenv("RESEND_API_KEY")
    sender = os.getenv("RESEND_SENDER")
    recipient = os.getenv("EMAIL_RECIPIENT")

    key_status = f"set ({len(api_key)} chars)" if api_key else "MISSING"
    print("Resend config:")
    print(f"  api_key   = {key_status}")
    print(f"  sender    = {sender}")
    print(f"  recipient = {recipient}")
    print()

    if not all([api_key, sender, recipient]):
        print("ERROR: missing RESEND_API_KEY, RESEND_SENDER, or EMAIL_RECIPIENT")
        return

    payload = {
        "from": f"IrieTrade <{sender}>",
        "to": [recipient],
        "subject": "[IrieTrade] Resend Primary Test",
        "html": "<h2>Resend works</h2><p>This is the primary path.</p>",
    }

    print("Sending via Resend API...")
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        print(f"Status: {resp.status_code}")
        print(f"Body:   {resp.text[:300]}")
        if resp.status_code in (200, 201):
            print()
            print("SUCCESS — check info.native254@gmail.com")
        else:
            print()
            print("FAILED — see error above.")
            if "domain" in resp.text.lower():
                print("Hint: verify irietrade.me is fully verified in Resend.")
    except Exception as e:  # noqa: BLE001
        print(f"FAILED — {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()