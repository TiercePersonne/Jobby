"""User authentication, password hashing, and session management for Jobby."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Dict, Optional


def hash_password(password: str) -> str:
    """Hashes a password using PBKDF2-HMAC-SHA256 with a random salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=100_000,
    )
    return f"{salt}${key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verifies a password against the stored salt$hash."""
    try:
        salt, expected_hash = hashed.split("$", 1)
        key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations=100_000,
        )
        return hmac.compare_digest(key.hex(), expected_hash)
    except Exception:
        return False


# In-memory session store (token -> {user_id, email, full_name, expires_at})
_SESSIONS: Dict[str, Dict] = {}
SESSION_DURATION_SECONDS = 60 * 60 * 24 * 30  # 30 days


def create_session_token(user_id: str, email: str, full_name: str = "") -> str:
    """Generates a secure cryptographically random session token."""
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = {
        "user_id": user_id,
        "email": email,
        "full_name": full_name,
        "expires_at": time.time() + SESSION_DURATION_SECONDS,
    }
    return token


def validate_session_token(token: Optional[str]) -> Optional[Dict]:
    """Validates a session token and returns the session payload if valid."""
    if not token or token not in _SESSIONS:
        return None
    session = _SESSIONS[token]
    if time.time() > session["expires_at"]:
        del _SESSIONS[token]
        return None
    return session


def delete_session_token(token: Optional[str]) -> None:
    """Removes a session token."""
    if token and token in _SESSIONS:
        del _SESSIONS[token]
