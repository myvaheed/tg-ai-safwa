"""The review screen: what the model proposed, and exactly Save or Discard."""

from __future__ import annotations

import html

from aiogram.types import InlineKeyboardMarkup, Message

from ...foundation.clock import utcnow
from ...foundation.errors import DomainError
from ...foundation.kinds import MessageKind
from ...telegram import (
    Services,
    edit_registered_message,
    send_registered,
    token_button,
)
from ..render import proposal_change_summary


async def render_proposal(
    message: Message,
    services: Services,
    proposal_id: int,
    *,
    replace_message_id: int | None = None,
    notice: str | None = None,
    event_id: str | None = None,
) -> None:
    proposal = services.root.reviews.proposal(proposal_id)
    if proposal is None:
        raise DomainError("Proposal is no longer pending")
    async with services.sessions() as session:
        changes = proposal.changes
        text_parts = ["<b>Review proposal</b>"]
        if notice:
            text_parts.append(html.escape(notice))
        text_parts.append(html.escape(proposal.message))
        # A proposal changes one item, so the feature that owns it draws the screen; one
        # that draws none gets the plain list, which knows nothing about the entities in it.
        presenter = services.root.proposals.presenter(changes[0].entity)
        screen = await presenter.screen(session, changes) if presenter is not None else None
        if screen is not None:
            text_parts[0] = f"<b>{screen.mode} {screen.item} · AI proposal</b>"
            text_parts.extend(screen.blocks)
            if screen.diffs:
                text_parts.append("<b>Proposed changes</b>\n" + "\n".join(screen.diffs))
        else:
            text_parts.append(
                "\n".join(f"• {html.escape(proposal_change_summary(change))}" for change in changes)
            )
        # Every proposal screen is read-only: exactly Save and Discard, never a field control.
        rows = [
            [
                await token_button(
                    session, services.owner_id, "✅ Save", "proposal_approve", {"id": proposal.id}
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "🗑 Discard",
                    "proposal_reject",
                    {"id": proposal.id},
                ),
            ]
        ]
        await session.commit()
    text = "\n\n".join(part for part in text_parts if part)
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    # Its time to be answered starts now, whether this is its first drawing or a redraw
    # after a press that failed.
    proposal.shown_at = utcnow()
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.APPROVAL,
            markup=markup,
            related_id=proposal_id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.APPROVAL,
            markup=markup,
            related_id=proposal_id,
            event_id=event_id,
        )
