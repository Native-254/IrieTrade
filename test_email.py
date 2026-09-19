#!/usr/bin/env python3
"""Test script to verify email functionality"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from monitoring.email_alerter import EmailAlerter

def test_email_alerter():
    """Test the email alerter with sample data"""
    print("Testing EmailAlerter...")

    # Initialize the email alerter
    e = EmailAlerter()

    # Test trade alert
    print("Sending test trade alert...")
    e.send_trade_alert("XRP/USDT", "BUY", 1.5, 1.32, source="kucoin")

    # Test African briefs
    print("Sending test African briefs...")
    e.send_africa_brief({
        "NSE":  {"report": "Top movers:\n• KCB 85.50 (+1.5%)\n• SCOM 24.15 (+1.2%)",
                 "insight": "KCB showing steady upward pressure on rising volume."},
        "JSE":  {"report": "Top movers:\n• AGL 879.07 (+2.5%)\n• AXX 1.56 (+6.1%)",
                 "insight": "Broad-based gains across mining names."},
    })

    print("Test emails sent. Check your Gmail inbox (should be in Updates tab).")

if __name__ == "__main__":
    test_email_alerter()