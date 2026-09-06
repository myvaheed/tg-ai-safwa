"""What a feature plugs into the chat: three ways a screen is reached, and the turn ending.

Wiring DTOs that both `Services` and `FeatureModule` name, so they sit under each and
import nothing of the package. What a screen *is* — an item that can be opened and cited —
is `foundation/screens.py`, where the engine may reach it too.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# (message, services) -> None. Run once the owner's turn has been answered, for work the
# application does on its own. The shell says the turn is over and reads nothing back.
AfterTurn = Callable[..., Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ScreenCommand:
    """One screen the owner opens by name: a slash command, a menu button, or both."""

    # (message, services) -> None.
    handler: Callable[..., Awaitable[None]]
    # Published to Telegram as `/<command>`; None means this screen has no command line.
    command: str | None = None
    description: str = ""
    # The action a button carries as `nav:<action>`; None means nothing navigates here.
    nav: str | None = None
    # What this screen is called wherever it is offered as a button.
    title: str | None = None


@dataclass(frozen=True, slots=True)
class StartLink:
    """A `/start <payload>` a feature answers itself, instead of it opening a cited item.

    `claims` also tells the command middleware to leave the open screen alone: such a tap
    replaces the screen it was made on rather than opening another one.
    """

    claims: Callable[[str], bool]
    # (message, services, payload) -> None.
    open: Callable[..., Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TextInputFlow:
    """What happens when the owner types a value into one editor.

    The editor itself is one screen: it validates, keeps itself alive on a refusal, and
    takes the typed message out of the chat. A flow is only the three things that differ —
    which validator this field wants, what the value is written to, and which screen the
    editor gives way to afterwards.
    """

    # Matches the flow the editor recorded in its UiSession state.
    name: str
    # (state) -> the validator for this field, or None to accept any trimmed text.
    validator: Callable[..., Any]
    # (services, state, value) -> None. Writes the value and ends the editor's session.
    apply: Callable[..., Awaitable[None]]
    # (message, services, state, value) -> None. Redraws the screen the editor replaced.
    render: Callable[..., Awaitable[None]]
