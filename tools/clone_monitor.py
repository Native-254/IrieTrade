#!/usr/bin/env python
"""Check GitHub clone stats and send an email if new clones appeared."""  # noqa: EXE001

import json
import os
from pathlib import Path

import requests

from utils.logger import log

OWNER = "Native-254"
REPO = "IrieTrade"
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "irietrade@example.com")
EMAIL_RECIPIENT = "info.native@gmail.com"
BREVO_API_KEY = os.getenv("EMAIL_BREVO_API_KEY", "")

STATE_FILE = Path("data/.clone_state.json")


def get_clone_count():
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/traffic/clones?per=day"
    headers = {"Accept": "application/vnd.github.v3+json"}
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code != 200:
        log.error(f"GitHub API error: {resp.status_code} {resp.text}")
        return None
    data = resp.json()
    total = sum(day.get("uniques", 0) for day in data.get("clones", []))
    return total


def send_notification(prev, current):
    if not BREVO_API_KEY:
        log.warning("Brevo API key not set – cannot send clone notification.")
        return
    payload = {
        "sender": {"email": EMAIL_SENDER, "name": "IrieTrade Clone Monitor"},
        "to": [{"email": EMAIL_RECIPIENT, "name": "Developer"}],
        "subject": f"📈 New clones on {REPO}!",
        "htmlContent": f"""
            <p>Your repo <strong>{OWNER}/{REPO}</strong> has new unique cloners.</p>
            <p>Previous count: {prev} &rarr; Current count: {current} (+{current - prev})</p>
            <p><a href=\"https://github.com/{OWNER}/{REPO}\">View repo</a></p>
        """,
    }
    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    if resp.status_code in (200, 201):
        log.success("Clone notification email sent.")
    else:
        log.error(f"Failed to send clone email: {resp.status_code} {resp.text}")


def main():
    current = get_clone_count()
    if current is None:
        return
    prev = 0
    if STATE_FILE.exists():
        try:
            prev = json.loads(STATE_FILE.read_text()).get("clones", 0)
        except json.JSONDecodeError:
            prev = 0
    if current > prev:
        send_notification(prev, current)
    STATE_FILE.write_text(json.dumps({"clones": current}))


if __name__ == "__main__":
    main()
