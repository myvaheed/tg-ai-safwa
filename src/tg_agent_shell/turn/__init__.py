"""One turn of the owner's, from the moment it is taken to the moment it is given back.

A turn is a process, not a feature: it serves every feature and its rules are written
under theirs, as `AG-TURN-*` in `tests/brd/agents.feature`. That is the shape `cues/`
already has.
"""

from __future__ import annotations

from .manager import TurnManager

__all__ = ["TurnManager"]
