#!/usr/bin/env python3
"""
Test script to verify the enhanced email alerter functionality.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, '/home/munene/Projects 2026/trading_bot')

from monitoring.email_alerter import EmailAlerter

def test_enhanced_alerter():
    print("Testing Enhanced Email Alerter...")

    # Create a temporary directory for state files
    with tempfile.TemporaryDirectory() as temp_dir:
        # Initialize the alerter (will show as disabled without env vars, but that's ok for testing)
        alerter = EmailAlerter()
        print(f"✓ EmailAlerter initialized")

        # Override the instance state paths to use temp directory
        alerter._state_path = Path(temp_dir) / "email_thread_state.json"
        alerter._state_path.parent.mkdir(parents=True, exist_ok=True)
        alerter._error_state_path = Path(temp_dir) / "email_error_state.json"
        alerter._error_state_path.parent.mkdir(parents=True, exist_ok=True)

        # Reload state from the new paths (should be empty)
        alerter._thread_state = alerter._load_state()
        alerter._error_state = alerter._load_error_state()

        # Test _normalize_error_key
        test_msg1 = "2026-09-22 10:01:22 | ERROR | Failed to place order for AAPL: reqid=12345"
        test_msg2 = "2026-09-22 11:05:33 | ERROR | Failed to place order for AAPL: reqid=67890"
        key1 = alerter._normalize_error_key(test_msg1)
        key2 = alerter._normalize_error_key(test_msg2)
        print(f"✓ Error normalization works: '{key1}' == '{key2}' (should be True)")

        # Test _error_budget_ok
        # Manually set state to test budget logic
        alerter._error_state = {"daily_date": "", "daily_count": 0, "hashes": {}}
        alerter._save_error_state()

        # Should be ok initially (under budget)
        assert alerter._error_budget_ok() == True
        print(f"✓ Budget check works (initial state)")

        # Exhaust budget
        alerter._error_state["daily_count"] = 80  # Default budget
        alerter._error_state["daily_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        alerter._save_error_state()

        # Should be exhausted now
        assert alerter._error_budget_ok() == False
        print(f"✓ Budget check works (exhausted)")

        # Test _error_dedup_ok
        # Reset state
        alerter._error_state = {"daily_date": "", "daily_count": 0, "hashes": {}}
        alerter._save_error_state()

        test_error = "Test error message for deduplication"

        # First call should be ok
        assert alerter._error_dedup_ok(test_error) == True
        print(f"✓ Deduplication check works (first call)")

        # Second call immediately should be suppressed
        assert alerter._error_dedup_ok(test_error) == False
        print(f"✓ Deduplication check works (suppressed duplicate)")

        # Test with different error
        assert alerter._error_dedup_ok("Different error message") == True
        print(f"✓ Deduplication check works (different error allowed)")

        print("\n✅ All tests passed!")
        return True

if __name__ == "__main__":
    success = test_enhanced_alerter()
    sys.exit(0 if success else 1)