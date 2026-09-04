"""What an agent's answer looks like in the chat, and how a decision resumes it.

An answer is either prose or a review screen, so the branch belongs here rather than in the
turn that asked for it, and so does the continuation an approved or discarded screen owes
the session it suspended.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram.enums import ChatAction
from aiogram.types import Message

from telegram_llm import markdown_to_telegram_html

from ...adapters.kinds import MessageKind
from ...ai.outcome import AIOutcome
from ...foundation.errors import failure_reason
from ...telegram import (
    Services,
    end_turn,
    open_citation,
    render_citations,
    send_prose,
    send_registered,
)
from ..model import DECISION_RECEIPTS, BatchDecision
from .screens import render_proposal

logger = logging.getLogger(__name__)


async def render_ai_outcome(
    message: Message,
    services: Services,
    outcome: AIOutcome,
    *,
    kind: MessageKind = MessageKind.DIALOGUE_ASSISTANT,
    event_id: str | None = None,
) -> None:
    """Render one agent state; suspended approval batches expose only their head item."""
    if outcome.proposal_id is not None:
        try:
            await render_proposal(message, services, outcome.proposal_id, event_id=event_id)
        except Exception:
            # A review is committed before its screen is drawn. One that never reached the
            # chat is unanswerable and holds the Cue gate shut for the life of the process,
            # so it ends here; saying what failed stays the caller's.
            try:
                await services.root.cancel_approval_for_proposal(outcome.proposal_id)
            except Exception:
                logger.exception("Could not end a review whose screen failed to send")
            raise
        return
    async with services.sessions() as session:
        text = await render_citations(
            session, services, markdown_to_telegram_html(outcome.message)
        )
    await send_prose(message, services, text, kind=kind, event_id=event_id)
    if outcome.open_item:
        await open_citation(message, services, outcome.open_item)


async def continue_agent_approval(
    message: Message,
    services: Services,
    proposal_id: int,
    *,
    decision: BatchDecision,
    result: dict[str, Any],
) -> bool:
    """Advance an open approval queue, resuming the model only after its last item."""
    if not services.root.has_pending_approval(proposal_id):
        return False
    resolved_text = f"{DECISION_RECEIPTS[decision]}."

    await send_registered(
        message,
        services,
        f"{resolved_text} Safwa is continuing…",
        kind=MessageKind.RECEIPT,
    )
    try:
        services.turn.begin(message.message_id)
    except Exception:
        logger.exception(
            "Could not acquire continuation lease after %s #%s", decision, proposal_id
        )
        await send_registered(
            message,
            services,
            f"{resolved_text}\n"
            "⚠️ The change is resolved, but the advisor follow-up was deferred. "
            "You can continue with a new message.",
            kind=MessageKind.DIALOGUE_ASSISTANT,
        )
        return True
    try:
        try:
            await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            outcome = await services.root.resolve_approval(
                proposal_id,
                decision=decision,
                result=result,
            )
        except Exception as error:
            logger.exception(
                "AI continuation failed after %s #%s", decision, proposal_id
            )
            await send_registered(
                message,
                services,
                f"{resolved_text}\n"
                "⚠️ The change is resolved, but Safwa could not generate its follow-up "
                f"({html.escape(failure_reason(error))}). "
                "You can continue with a new message.",
                kind=MessageKind.DIALOGUE_ASSISTANT,
            )
            return True
        if outcome is None:
            return False
        await render_ai_outcome(message, services, outcome)
        return True
    finally:
        await end_turn(message, services)
