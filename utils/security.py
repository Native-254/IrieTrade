# utils/security.py
import os
from cryptography.fernet import Fernet
from pathlib import Path

# Load or generate a persistent encryption key
def _get_or_create_key() -> bytes:
    key_path = Path('data/.encryption_key')
    if key_path.exists():
        return key_path.read_bytes()
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    os.chmod(key_path, 0o600)
    return key

_cipher = Fernet(_get_or_create_key())

def encrypt(value: str) -> str:
    """Encrypt a string and return an 'enc:'-prefixed token."""
    encrypted = _cipher.encrypt(value.encode())
    return f"enc:{encrypted.decode()}"

def decrypt(maybe_encrypted: str) -> str:
    """Decrypt if the string starts with 'enc:', else return as-is."""
    if not isinstance(maybe_encrypted, str) or not maybe_encrypted.startswith("enc:"):
        return maybe_encrypted
    return _cipher.decrypt(maybe_encrypted[4:].encode()).decode()