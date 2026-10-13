"""Secret words are exact text; only a salted verifier is stored."""

from __future__ import annotations

import hashlib
import hmac
import secrets

# A text this short, or this few edits from the word, may be a try at the word.
SECRET_LIKE_LENGTH = 5
SECRET_LIKE_DISTANCE = 3


def _digest(word: str, salt: bytes) -> bytes:
    return hashlib.scrypt(word.encode("utf-8"), salt=salt, n=16384, r=8, p=1)


def hash_secret_word(word: str) -> str:
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{_digest(word, salt).hex()}"


def matches_secret_word(word: str, verifier: str) -> bool:
    salt, expected = verifier.split(":")
    return hmac.compare_digest(_digest(word, bytes.fromhex(salt)), bytes.fromhex(expected))


def resembles_secret_word(text: str, word: str) -> bool:
    """Whether a text may be a try at the word: too short, or too few edits away from it."""
    return len(text) <= SECRET_LIKE_LENGTH or _edits(text, word) <= SECRET_LIKE_DISTANCE


def _edits(first: str, second: str) -> int:
    """The Levenshtein distance: insertions, deletions and replacements, one each."""
    previous = list(range(len(second) + 1))
    for row, left in enumerate(first, 1):
        current = [row]
        for column, right in enumerate(second, 1):
            current.append(
                min(previous[column] + 1, current[-1] + 1, previous[column - 1] + (left != right))
            )
        previous = current
    return previous[-1]
