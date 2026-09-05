import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

import jwt

AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "dev-secret-change-this-before-any-real-deployment")
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", str(7 * 24 * 3600)))  # 7 days


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 with a random salt, standard library only. This
    deliberately avoids bcrypt/argon2 (compiled dependencies that can be
    slow or fail to install in a time-constrained setup) while still being
    a real, slow, salted hash - not plaintext, not a fast unsalted hash."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$")
        expected = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000)
        return hmac.compare_digest(expected.hex(), digest_hex)
    except Exception:
        return False


def create_token(profile_id: str) -> str:
    payload = {"sub": profile_id, "exp": int(time.time()) + TOKEN_TTL_SECONDS}
    return jwt.encode(payload, AUTH_SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=["HS256"])
        return payload.get("sub")
    except Exception:
        return None
