"""Secret words are exact text; only a salted verifier is stored."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def _digest(word: str, salt: bytes) -> bytes:
    return hashlib.scrypt(word.encode("utf-8"), salt=salt, n=16384, r=8, p=1)


def hash_secret_word(word: str) -> str:
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{_digest(word, salt).hex()}"


def matches_secret_word(word: str, verifier: str) -> bool:
    salt, expected = verifier.split(":")
    return hmac.compare_digest(_digest(word, bytes.fromhex(salt)), bytes.fromhex(expected))
