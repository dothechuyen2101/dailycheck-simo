"""
Security helpers for encrypting stored credentials.
"""
import base64
import hashlib

from cryptography.fernet import Fernet

from src.config_manager import get


def _build_fernet() -> Fernet:
    secret = (get("security.encryption_key", "") or "").encode("utf-8")
    if not secret:
        raise ValueError(
            "security.encryption_key is required to store dynamic module passwords"
        )
    digest = hashlib.sha256(secret).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _build_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _build_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
