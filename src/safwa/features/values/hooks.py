"""Clarify a saved Value link only when its supporting direction is unclear.

The hook's work checks each new link with a small model and keeps the unclear pair as its
pending request; every pending pair is worded in one request, which waits behind the Cue gate
like any hook's, and the hook's switch drops what is still pending.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import HookSpec, OnCommitted, RunAndAdvise, RunContext

from ..cards.api import Card
from ..checks.model import Check
from .api import CARD_VALUE_LINKED, CHECK_VALUE_LINKED
from .link_review import ValueLinkReviewer
from .model import CardValue, CheckValue, Value

CLARIFY_REQUEST = (
    "Saved Value links that are unclear or reversed:\n"
    "{links}\n"
    "Read the current items and Values first.\n"
    "If a link is gone or its Note already makes the supporting direction clear, skip it.\n"
    "Briefly ask how each item supports its Value and help clarify Note.\n\n"
    'Example Goal "Earn $4k" linked to Health:\n'
    "- VALID: money pays for healthcare.\n"
    "- INVALID: health is needed to earn money.\n"
    "- UNCLEAR: no context shows which meaning the user intends.\n\n"
    "If you have a useful possible explanation, suggest it briefly and ask "
    "whether it fits the user's meaning.\n"
    "Present it as a possibility, not as the user's reason.\n"
    "Change nothing until they answer."
)


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


async def review_link(
    event: Committed, context: RunContext[ValueLinkResources]
) -> tuple[dict[str, Any], ...]:
    """The pair to ask about when the link is unclear and still what the model read."""
    item_type = "card" if event.kind == CARD_VALUE_LINKED else "check"
    value_id = event.data["value_id"]
    async with context.sessions() as session:
        asked = await _read_link(session, item_type, event.subject_id, value_id)
    if asked is None or await context.resources.value_link_reviewer.clear(asked):
        return ()
    async with context.sessions() as session:
        if await _read_link(session, item_type, event.subject_id, value_id) != asked:
            return ()
    return ({"type": item_type, "id": event.subject_id, "value_id": value_id},)


async def clarification_request(
    session: AsyncSession, items: Sequence[dict[str, Any]]
) -> str | None:
    """One request for every pair still linked, or nothing when all are gone."""
    links = []
    for item in items:
        link = await _read_link(session, item["type"], item["id"], item["value_id"])
        if link is not None:
            links.append(
                f'- [{link["title"]}]({item["type"]}:{item["id"]}) → '
                f'[{link["value"]}](value:{item["value_id"]})'
            )
    return CLARIFY_REQUEST.format(links="\n".join(links)) if links else None


VALUE_LINK_HOOK = HookSpec(
    name="values.link_clarification",
    owner="values",
    on=(OnCommitted(CARD_VALUE_LINKED), OnCommitted(CHECK_VALUE_LINKED)),
    evaluate=linked_value,
    effect=RunAndAdvise(run=review_link, prepare=clarification_request),
    title="Value link clarification",
    description=(
        "After linking a Value, checks whether the Goal, Action or Check supports it. "
        "Asks to clarify Note only when the link is unclear or reversed."
    ),
)
