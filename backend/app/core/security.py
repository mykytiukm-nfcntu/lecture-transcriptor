"""Password hashing (Argon2) and opaque session-token generation."""
from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plaintext: str) -> str:
    """Return an Argon2id hash of `plaintext` suitable for storage."""
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    """Return True iff `plaintext` matches the stored Argon2 hash."""
    try:
        _hasher.verify(hashed, plaintext)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def generate_token() -> str:
    """Return a fresh URL-safe opaque token for a session row."""
    return secrets.token_urlsafe(32)
