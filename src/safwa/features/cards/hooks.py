"""After an Action is blocked, the Advisor is asked whether to offer a Reminder."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import Advise, HookSpec, HookSwitch, OnCommitted

from .model import TERMINAL_STAGES, Card
from .use_cases import CARD_BLOCKED

BLOCKER_REQUEST = (
    "Blocked since we last spoke:\n{cards}\n"
    "Ask the user whether to set a Reminder to come back to each; if they want one, agree "
    "when and propose it. Do not create anything without their answer."
)


async def blocked_cards(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


async def blocker_request(session: AsyncSession, items: Sequence[int]) -> str | None:
    """The request about the Cards that are still blocked and still open, or nothing."""
    cards = list(
        await session.scalars(
            select(Card)
            .where(
                Card.id.in_([int(item) for item in items]),
                Card.blocked.is_(True),
                Card.archived_at.is_(None),
                Card.effective_stage.not_in([stage.value for stage in TERMINAL_STAGES]),
            )
            .order_by(Card.id)
        )
    )
    if not cards:
        return None
    lines = "\n".join(f"- #{card.id} «{card.title}»: {card.blocked_description}" for card in cards)
    return BLOCKER_REQUEST.format(cards=lines)


BLOCKER_HOOK = HookSpec(
    name="cards.blocker",
    owner="cards",
    on=(OnCommitted(kind=CARD_BLOCKED),),
    evaluate=blocked_cards,
    effect=Advise(prepare=blocker_request),
    switch=HookSwitch(
        title="Blocker follow-up",
        description="After an Action is blocked, asks whether to set a Reminder to come back to it.",
    ),
)
