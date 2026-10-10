"""Clarify a saved Value link only when its supporting direction is unclear."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.cues.queue import add_cue
from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import HookSpec, OnCommitted, Run, RunContext

from ..cards.api import Card
from ..checks.model import Check
from .api import CARD_VALUE_LINKED, CHECK_VALUE_LINKED
from .link_review import ValueLinkReviewer
from .model import CardValue, CheckValue, Value


class ValueLinkResources(Protocol):
    value_link_reviewer: ValueLinkReviewer


async def linked_value(event: Committed) -> tuple[Committed, ...]:
    return (event,)


async def _read_link(
    session: AsyncSession, item_type: str, item_id: int, value_id: int
) -> dict[str, Any] | None:
    model, junction = (Card, CardValue) if item_type == "card" else (Check, CheckValue)
    item = await session.get(model, item_id)
    value = await session.get(Value, value_id)
    link = await session.get(junction, {f"{item_type}_id": item_id, "value_id": value_id})
    if item is None or item.archived_at is not None or value is None or link is None:
        return None
    return {
        "kind": item.kind if item_type == "card" else "check",
        "title": item.title,
        "note": item.note,
        "value": value.name,
        "value_description": value.description,
    }


async def review_link(event: Committed, context: RunContext[ValueLinkResources]) -> None:
    item_type = "card" if event.kind == CARD_VALUE_LINKED else "check"
    value_id = event.data["value_id"]
    async with context.sessions() as session:
        asked = await _read_link(session, item_type, event.subject_id, value_id)
    if asked is None or await context.resources.value_link_reviewer.clear(asked):
        return
    async with context.sessions() as session:
        if await _read_link(session, item_type, event.subject_id, value_id) != asked:
            return
        await add_cue(
            session,
            text=(
                f'A saved Value link is unclear or reversed: '
                f'[{asked["title"]}]({item_type}:{event.subject_id}) → '
                f'[{asked["value"]}](value:{value_id}).\n'
                "Read the current item and Value first.\n"
                "If the link is gone or Note already makes the supporting direction clear, "
                "skip this clarification.\n"
                "Briefly ask how the item supports the Value and help clarify Note.\n\n"
                'Example Goal "Earn $4k" linked to Health:\n'
                "- VALID: money pays for healthcare.\n"
                "- INVALID: health is needed to earn money.\n"
                "- UNCLEAR: no context shows which meaning the user intends.\n\n"
                "If you have a useful possible explanation, suggest it briefly and ask "
                "whether it fits the user's meaning.\n"
                "Present it as a possibility, not as the user's reason.\n"
                "Change nothing until they answer."
            ),
        )
        await session.commit()


VALUE_LINK_HOOK = HookSpec(
    name="values.link_clarification",
    owner="values",
    on=(OnCommitted(CARD_VALUE_LINKED), OnCommitted(CHECK_VALUE_LINKED)),
    evaluate=linked_value,
    effect=Run(review_link),
    title="Value link clarification",
    description=(
        "After linking a Value, checks whether the Goal, Action or Check supports it. "
        "Asks to clarify Note only when the link is unclear or reversed."
    ),
)
