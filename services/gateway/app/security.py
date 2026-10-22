"""All cryptography in one place.

* Passwords: scrypt (memory-hard, from Python's standard library) with a random salt.
* API keys:  "mgw_" + 32 random bytes. Only SHA-256(key) is stored. A fast hash is fine
             here because the key itself has 256 bits of entropy, so it cannot be
             brute-forced (unlike a human password). Lookup is a single indexed query.
* Upstream credentials: encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).
             MultiFernet allows key rotation: the first key encrypts, any key decrypts.
* Sessions:  short-lived HS256 JWTs in an httpOnly cookie.
* Internal chat tokens: 5-minute JWTs that let the chat agent call /mcp as the logged-in user.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

API_KEY_PREFIX = "mgw_"
SESSION_AUDIENCE = "dashboard"
INTERNAL_AUDIENCE = "mcp-internal"

# ---------------------------------------------------------------------------- passwords
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p),
                                dklen=len(digest_hex) // 2)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------- API keys
def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext_key, prefix_for_display, sha256_hash)."""
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[:12], hash_token(key)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_invite_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(24)
    return token, hash_token(token)


# ---------------------------------------------------------------------------- encryption
def _fernet_key(material: str) -> bytes:
    # Accept any high-entropy string and derive a valid 32-byte Fernet key from it.
    return base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())


class CredentialCipher:
    def __init__(self, keys_csv: str) -> None:
        keys = [k.strip() for k in keys_csv.split(",") if k.strip()]
        if not keys:
            raise RuntimeError("ENCRYPTION_KEYS is empty: refusing to store upstream credentials unencrypted")
        self._f = MultiFernet([Fernet(_fernet_key(k)) for k in keys])

    def encrypt(self, plaintext: str) -> str:
        return self._f.encrypt(plaintext.encode()).decode() if plaintext else ""

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._f.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Cannot decrypt upstream credential (wrong ENCRYPTION_KEYS?)") from exc

    def rotate(self, token: str) -> str:
        return self._f.rotate(token.encode()).decode() if token else ""


# ---------------------------------------------------------------------------- JWTs
def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session_token(secret: str, user_id: int, hours: int) -> str:
    payload = {"sub": str(user_id), "aud": SESSION_AUDIENCE, "iat": _now(), "exp": _now() + timedelta(hours=hours)}
    return jwt.encode(payload, secret, algorithm="HS256")


def create_internal_token(secret: str, user_id: int, minutes: int = 5) -> str:
    payload = {"sub": str(user_id), "aud": INTERNAL_AUDIENCE, "src": "chat", "iat": _now(),
               "exp": _now() + timedelta(minutes=minutes)}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: str, token: str, audience: str) -> int | None:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], audience=audience)
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
