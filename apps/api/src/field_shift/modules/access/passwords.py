"""scrypt with OWASP's 32 MiB, r=8, p=3 setting and unique salts."""

import hashlib
import hmac
import secrets
from threading import BoundedSemaphore

from fastapi import HTTPException

_hash_slots = BoundedSemaphore(2)


def password_hash(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    if not _hash_slots.acquire(timeout=1):
        raise HTTPException(
            503, "Sign-in is busy. Try again shortly.", headers={"Retry-After": "2"}
        )
    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**15, r=8, p=3, maxmem=48 * 1024**2, dklen=32
        )
    finally:
        _hash_slots.release()
    return f"scrypt-v1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        version, salt, digest = encoded.split("$")
        if version != "scrypt-v1" or len(salt) != 32 or len(digest) != 64:
            return False
        return hmac.compare_digest(password_hash(password, salt=bytes.fromhex(salt)), encoded)
    except (ValueError, TypeError):
        return False


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(session_token: str) -> str:
    return digest_token("field-shift-csrf:" + session_token)
