# utils/security.py
"""Symmetric encryption / decryption for sensitive values in .env."""
import os
from pathlib import Path

from cryptography.fernet import Fernet

_KEY_PATH = Path("data/.encryption_key")


def _get_or_create_key() -> bytes:
    """Load the encryption key from disk or create a new one."""
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _KEY_PATH.exists():
        return _KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    _KEY_PATH.write_bytes(key)
    return key


_fernet = Fernet(_get_or_create_key())


def encrypt(value: str) -> str:
    """Return base64‑encoded encrypted string."""
    return _fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(value: str) -> str:
    """Decrypt a previously encrypted string back to plain text.

    If the value is not a valid Fernet token (e.g. old plain text still
    present in .env), it is returned unchanged for backwards compatibility.
    """
    try:
        return _fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except Exception:  # noqa: BLE001
        # value was not encrypted – return as‑is
        return value


def safe_env(key: str, default: str | None = None) -> str | None:
    """Return os.environ[key] after decryption, or default."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    return decrypt(raw)