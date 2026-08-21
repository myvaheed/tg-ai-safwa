"""Errors shared by Safwa business features."""

from __future__ import annotations


class DomainError(ValueError):
    pass


class StaleStateError(DomainError):
    pass
