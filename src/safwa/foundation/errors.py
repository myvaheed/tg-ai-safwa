"""Errors shared by Safwa business features."""

from __future__ import annotations


class DomainError(ValueError):
    pass


class StaleStateError(DomainError):
    pass


def failure_reason(error: Exception, limit: int = 160) -> str:
    """One short owner-readable clause; the traceback stays in the log."""
    text = " ".join(str(error).split()) or type(error).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"
