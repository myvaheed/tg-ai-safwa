"""A Thought's two explicit requests, from its review or its saved screen."""

from __future__ import annotations

import json
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.proposals.api import ChangeAction, ProposalAction, ProposalChange
from tg_agent_shell.proposals.model import BatchDecision
from tg_agent_shell.telegram import (
    Place,
    Services,
    back_button,
    paginate,
    paging_row,
    place_button,
    send_registered,
    token_button,
)
from tg_agent_shell.telegram.chat import dismiss_prior_ui, send_owner_turn
from tg_agent_shell.telegram.dialogue import run_dialogue_turn
from tg_agent_shell.telegram.services import CallbackContext
from tg_agent_shell.telegram.subsessions import EXIT_RESULT, finish_subsession

from ..model import Card, CardKind
from .presentation import item_button_label

QUALITY_PLAN = "Quality Plan"
DIRECT_CREATE = "Direct Create"
THOUGHTS_PAGE_SIZE = 25


async def command_thoughts(message: Message, services: Services, *, page: int = 0) -> None:
    async with services.sessions() as session:
        cards = list(await session.scalars(
            select(Card).where(Card.kind == CardKind.THOUGHT).order_by(Card.created_at, Card.id)
        ))
        shown = paginate(cards, page, THOUGHTS_PAGE_SIZE)
        here = Place("thought_list", {"page": shown.index})
        rows = [
            [await place_button(
                session, services.owner_id, await item_button_label(session, card),
                here.child("card_view", id=card.id),
            )]
            for card in shown.items
        ]
        rows.extend(await paging_row(session, services.owner_id, shown, here))
        rows.append([await place_button(session, services.owner_id, "↻ Refresh", here)])
        rows.append([await back_button(session, services.owner_id, None)])
        await session.commit()
    await send_registered(
        message, services, f"<b>Thoughts</b> · {len(cards)} · {shown.label}",
        kind=MessageKind.DASHBOARD, markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def on_thought_list(context: CallbackContext) -> None:
    await command_thoughts(
        context.message, context.services, page=int(context.payload.get("page", 0))
    )


def thought_actions(change: ProposalChange) -> tuple[tuple[ProposalAction, ...], ...]:
    if change.action is not ChangeAction.CREATE or change.values.get("kind") != CardKind.THOUGHT:
        return ()
    return ((
        ProposalAction(QUALITY_PLAN, "card_thought_process", {"plan": True, "proposal": True}),
        ProposalAction(DIRECT_CREATE, "card_thought_process", {"plan": False, "proposal": True}),
    ),)


async def thought_buttons(session: AsyncSession, services: Services, card_id: int) -> list[InlineKeyboardButton]:
    return [
        await token_button(
            session, services.owner_id, label, "card_thought_process",
            {"id": card_id, "plan": plan},
        )
        for label, plan in ((QUALITY_PLAN, True), (DIRECT_CREATE, False))
    ]


def thought_request(values: dict[str, Any], *, plan: bool) -> str:
    instruction = (
        "Quality Plan. Plan this Thought with me. Open Blueprint."
        if plan else "Direct Create. Carry out the request in this Thought. Create the items it describes."
    )
    content = {name: values.get(name) or "" for name in ("title", "note", "blocked_description")}
    return instruction + "\n" + json.dumps(content, ensure_ascii=False)


async def process_thought(context: CallbackContext) -> None:
    services = context.services
    if not services.turn.try_begin(context.message.message_id):
        return
    try:
        proposal_id = int(context.payload["id"])
        kept = False
        async with context.sessions() as session:
            revision = (await services.root.proposals.world(session)).revision
            if context.payload.get("proposal"):
                proposal = services.root.reviews.proposal(proposal_id)
                if proposal is None or not services.root.proposals.offered_actions(proposal.changes):
                    raise DomainError("This Thought proposal is no longer available.")
                values = dict(proposal.changes[0].values)
                stale = revision != proposal.workspace_revision
                batch = services.root.reviews.batch_for_proposal(proposal_id)
                kept = batch is not None and any(
                    item.decision is BatchDecision.APPROVED for item in batch.state.items
                )
            else:
                card = await session.get(Card, proposal_id)
                if card is None or card.kind != CardKind.THOUGHT:
                    raise DomainError("Thought does not exist.")
                values = {name: getattr(card, name) for name in ("title", "note", "blocked_description")}
                stale = False
        await dismiss_prior_ui(context.message, services)
        if stale:
            raise DomainError("The workspace changed. Ask Safwa to propose this Thought again.")
        if kept:
            # Kept workspace Saves are atomic and each bumps the revision; a rollback bumps none.
            async with context.sessions() as session:
                if (await services.root.proposals.world(session)).revision == revision:
                    raise DomainError("The earlier Saves failed. The Thought was not processed.")
        await finish_subsession(context.message, services, EXIT_RESULT)
        request = thought_request(values, plan=bool(context.payload["plan"]))
        sent = await send_owner_turn(context.message, services, request)
        await run_dialogue_turn(context.message, services, request, sent.message_id)
    finally:
        services.turn.end(context.message.message_id)
