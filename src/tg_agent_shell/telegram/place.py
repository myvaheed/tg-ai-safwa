"""A screen's address, and the way back that travels with it.

A screen is drawn by one callback action from its payload, so that action and payload are
its address: a `Place`. A Place also names the Place it was entered from, so the way back
from a screen is carried by the button that leads to it. The buttons and links built from a
Place are `navigation.py`; this module imports nothing, so the context a button hands its
handler can read its Place too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# How many screens the way back remembers. An older one is forgotten, and the way back
# from the oldest one kept is the menu.
NAV_DEPTH = 10


@dataclass(frozen=True, slots=True)
class Place:
    """One screen's address: the action that draws it, its arguments, and where it was
    entered from. `back` None means nothing stands behind it, and its way back is the menu."""

    action: str
    args: Mapping[str, Any] = field(default_factory=dict)
    back: Place | None = None

    @classmethod
    def of(cls, action: str, payload: Mapping[str, Any]) -> Place:
        """The Place a button's action and payload name."""
        return cls(
            action,
            {key: value for key, value in payload.items() if key != "back"},
            cls.at(payload.get("back")),
        )

    @classmethod
    def at(cls, address: Any) -> Place | None:
        """The Place an `address` names, or None when it names none."""
        if not isinstance(address, Mapping) or not address.get("action"):
            return None
        return cls.of(
            str(address["action"]), {k: v for k, v in address.items() if k != "action"}
        )

    def child(self, action: str, **args: Any) -> Place:
        """A screen entered from this one."""
        return Place(action, args, self)

    def but(self, **args: Any) -> Place:
        """This screen in another state, such as another page: the way back stays."""
        return Place(self.action, {**self.args, **args}, self.back)

    @property
    def payload(self) -> dict[str, Any]:
        """What a button to this Place carries beside its action."""
        return _payload(self, NAV_DEPTH)

    @property
    def address(self) -> dict[str, Any]:
        """This Place as one value, to keep where a button cannot carry it: `at` reads it."""
        return {"action": self.action, **self.payload}


def _payload(place: Place, depth: int) -> dict[str, Any]:
    payload = dict(place.args)
    if place.back is not None and depth > 1:
        payload["back"] = {"action": place.back.action, **_payload(place.back, depth - 1)}
    return payload
