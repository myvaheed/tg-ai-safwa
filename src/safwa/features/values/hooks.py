"""Follow a Value link both ways: clarify a saved one only when its supporting direction is
unclear, and propose a Note without the Value once the link is removed.

Each hook's work checks with a small model and keeps what needs words as its pending request;
every pending item is worded in one request, which waits behind the Cue gate like any hook's,
and the hook's switch drops what is still pending. A removal hands on one fact for every item
one change took the Value off, so the items of one deletion are one request.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.hooks.contracts import HookSpec, OnCommitted, RunAndAdvise, RunContext

from ..cards.api import Card
from ..checks.model import Check
from .api import CARD_VALUE_LINKED, CHECK_VALUE_LINKED, VALUE_UNLINKED
from .link_review import ValueLinkReviewer
from .model import CardValue, CheckValue, Value

CLARIFY_REQUEST = (
    "Saved Value links that are unclear or reversed:\n"
    "{links}\n"
    "Read the current items and Values first.\n"
    "If a link is gone or its Note already makes the supporting direction clear, skip it.\n"
    "Briefly ask how each item supports its Value and help clarify Note.\n"
    "If you have a useful possible explanation, suggest it briefly and ask "
    "whether it fits the user's meaning.\n"
    "Present it as a possibility, not as the user's reason.\n"
    "Change nothing until they answer."
)

CLEANUP_REQUEST = (
    "These items no longer carry a Value, and their Note still mentions it:\n"
    "{items}\n"
    "Read their current Note first.\n"
    "Skip an item that carries the Value again or whose Note no longer mentions it.\n"
    "Route to workspace_mutator to propose each Note without the part about that Value.\n"
    "Keep the rest of each Note word for word."
)


class ValueLinkResources(Protocol):
    value_link_reviewer: ValueLinkReviewer


async def changed_link(event: Committed) -> tuple[Committed, ...]:
    return (event,)


async def _pair(
    session: AsyncSession, item_type: str, item_id: int, value_id: int
) -> tuple[Card | Check | None, CardValue | CheckValue | None]:
    """The live item, and its link to the Value if it has one."""
    model, junction = (Card, CardValue) if item_type == "card" else (Check, CheckValue)
    item = await session.get(model, item_id)
    if item is None or item.archived_at is not None:
        return None, None
    return item, await session.get(junction, {f"{item_type}_id": item_id, "value_id": value_id})


def _kind(item: Card | Check) -> str:
    return item.kind if isinstance(item, Card) else "check"


async def _read_link(
    session: AsyncSession, item_type: str, item_id: int, value_id: int
) -> dict[str, Any] | None:
    item, link = await _pair(session, item_type, item_id, value_id)
    value = await session.get(Value, value_id)
    if item is None or value is None or link is None:
        return None
    return {
        "kind": _kind(item),
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
    evaluate=changed_link,
    effect=RunAndAdvise(run=review_link, prepare=clarification_request),
    title="Value link clarification",
    description=(
        "After linking a Value, checks whether the Goal, Action or Check supports it. "
        "Asks to clarify Note only when the link is unclear or reversed."
    ),
)


async def _read_unlinked(
    session: AsyncSession, item_type: str, item_id: int, value_id: int
) -> dict[str, Any] | None:
    """The item a Value left, while it has a Note and does not carry the Value again."""
    item, link = await _pair(session, item_type, item_id, value_id)
    if item is None or link is not None or not item.note.strip():
        return None
    return {"kind": _kind(item), "title": item.title, "note": item.note}


async def review_unlinked(
    event: Committed, context: RunContext[ValueLinkResources]
) -> tuple[dict[str, Any], ...]:
    """The items whose Note still mentions the Value and is still what the model read."""
    value = {"value": event.data["value"], "value_description": event.data["value_description"]}
    found = []
    for item in event.data["items"]:
        async with context.sessions() as session:
            asked = await _read_unlinked(session, item["type"], item["id"], event.subject_id)
        if asked is None or not await context.resources.value_link_reviewer.mentioned(
            {**asked, **value}
        ):
            continue
        async with context.sessions() as session:
            if await _read_unlinked(session, item["type"], item["id"], event.subject_id) != asked:
                continue
        found.append({**item, "value_id": event.subject_id, "value": event.data["value"]})
    return tuple(found)


async def cleanup_request(session: AsyncSession, items: Sequence[dict[str, Any]]) -> str | None:
    """One request for every item still without the Value and with a Note, or nothing."""
    lines = []
    for item in items:
        left = await _read_unlinked(session, item["type"], item["id"], item["value_id"])
        if left is not None:
            lines.append(
                f'- [{left["title"]}]({item["type"]}:{item["id"]}): Value «{item["value"]}»'
            )
    return CLEANUP_REQUEST.format(items="\n".join(lines)) if lines else None


VALUE_UNLINK_HOOK = HookSpec(
    name="values.unlink_cleanup",
    owner="values",
    on=(OnCommitted(VALUE_UNLINKED),),
    evaluate=changed_link,
    effect=RunAndAdvise(run=review_unlinked, prepare=cleanup_request),
    title="Value unlink cleanup",
    description=(
        "After a Value is taken off a Goal, Action or Check, or deleted, checks whether its "
        "Note still mentions the Value. Proposes that Note without it."
    ),
)
