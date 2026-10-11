"""The access state."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Open:
    pass


@dataclass(frozen=True, slots=True)
class Locked:
    pass


@dataclass(frozen=True, slots=True)
class Unlocking:
    pass


type AccessState = Open | Locked | Unlocking
