"""The page on what Effort Points mean (CD-EFFORT-073).

The rungs are the Card's own scale, so the page is drawn here; Profile, where Effort Points
are switched on, opens it beside the switch. A proposal's Edit shows the same words in place
of its review, and the Effort Points reminder cites it.
"""

from __future__ import annotations

import html
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import CallbackContext, Place, Services, back_button, send_registered

from ...profile.model import UserProfile
from ..api import EFFORT_POINTS_PAGE
from ..model import EFFORT_RUNGS, effort_label

EFFORT_POINTS_TEXT = "\n".join(
    [
        "<b>🔢 Effort Points</b>",
        "How much an Action takes out of you and the rest you need after it: physical, mental "
        "or emotional load, not hours or importance.",
        "",
        "Pick the rung that fits how you are once the whole Action is done, in your usual "
        "state:",
        *(
            f"{effort_label(points)} — {html.escape(words)}."
            for points, words in EFFORT_RUNGS.items()
        ),
        "",
        "On a new Action, Effort is an estimate. When it is Done, set what it really took.",
        "For a repeating Action, rate one occurrence.",
        "Effort Points feed the Sprint's capacity and the Today overload warning.",
    ]
)


async def render_effort_points_page(
    message: Message, services: Services, *, back: Place | None = None, replace: bool | None = None
) -> None:
    """The page, with the way back to where it was opened, or to the menu."""
    async with services.sessions() as session:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[await back_button(session, services.owner_id, back)]]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        EFFORT_POINTS_TEXT,
        kind=MessageKind.DASHBOARD,
        markup=markup,
        replace=replace,
    )


async def open_effort_points_page(
    message: Message, services: Services, item_id: int, *, replace: bool | None = None
) -> None:
    """The page a citation of it opens; the id is the Profile's, which says nothing more."""
    del item_id
    await render_effort_points_page(message, services, replace=replace)


async def effort_points_label(
    session: AsyncSession, services: Any, profile: UserProfile, *, short: bool
) -> None:
    """A citation of the page keeps the words it was cited with."""
    del session, services, profile, short


async def _on_page(context: CallbackContext) -> None:
    await render_effort_points_page(context.message, context.services, back=context.back)


EFFORT_PAGE_ACTIONS = {EFFORT_POINTS_PAGE: _on_page}
