"""The review screen: what the model proposed, and exactly Save or Discard."""

from __future__ import annotations

import html

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.clock import utcnow
from ...foundation.errors import DomainError
from ...foundation.kinds import MessageKind
from ...telegram import (
    Services,
    edit_registered_message,
    render_citations,
    send_registered,
    token_button,
)
from ..api import Effect, ProposalScreen, ShownField, effect_of
from ..marks import field_line, shown
from ..model import ChangeAction, ProposalChange
from ..render import detail_label, result_value

SIMILAR_HEADING = "<b>⚠️ Similar items already exist</b>"
SIMILAR_ITEMS_SHOWN = 3

EFFECT_ICONS = {
    Effect.CREATE: "➕",
    Effect.UPDATE: "✏️",
    Effect.ARCHIVE: "📦",
    Effect.DELETE: "🗑",
}


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
        # A proposal changes one item, so the feature that owns it draws the screen; one
        # that draws none gets each value it sets, which knows nothing about the entity.
        presenter = services.root.proposals.presenter(changes[0].entity)
        screen = await presenter.screen(session, changes) if presenter is not None else None
        screen = screen or _plain_screen(changes)
        effect = screen.effect or effect_of(changes)
        heading = (
            f"<b>{EFFECT_ICONS[effect]} {html.escape(screen.mode or effect.value)} "
            f"{html.escape(screen.item)}</b>"
        )
        text_parts = [f"{heading} · {proposal.place}" if proposal.place else heading]
        if notice:
            text_parts.append(html.escape(notice))
        text_parts.append(await similar_items(session, services, changes[0]))
        text_parts.append(
            "\n".join(line for field in screen.fields if (line := field_line(field, effect)))
        )
        text_parts.extend(screen.blocks)
        # Every proposal screen is read-only: exactly Save and Discard, never a field control.
        # Save is named for what it does to the item.
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"✅ {effect.value}",
                    "proposal_approve",
                    {"id": proposal.id},
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
        # An item a review names is not a link: tapping it would leave the review unanswered.
        text = await render_citations(
            session, services, "\n\n".join(part for part in text_parts if part), plain=True
        )
        await session.commit()
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


def _plain_screen(changes: list[ProposalChange]) -> ProposalScreen:
    """The screen of a proposal whose feature draws none: each value it sets, as it is set."""
    return ProposalScreen(
        item=changes[0].entity.replace("_", " ").title(),
        fields=tuple(
            ShownField(detail_label(field), shown(value), shown(value), anchor=True)
            for change in changes
            for field, value in change.values.items()
        ),
    )


async def similar_items(session: AsyncSession, services: Services, change: ProposalChange) -> str:
    """The open items of a new item's entity most like it, or nothing (PR-SIMILAR-030)."""
    if change.action is not ChangeAction.CREATE or services.search is None:
        return ""
    similar = services.root.proposals.similar.get(change.entity)
    if similar is None:
        return ""
    items = await similar.open_items(session)
    words = dict(items)
    ids = await services.search.alike(
        change.entity, similar.field, str(change.values.get(similar.field) or ""), words
    )
    if not ids:
        return ""
    lines = [
        f"• [{html.escape(result_value(words[item_id]))}]({change.entity}:{item_id})"
        for item_id in ids[:SIMILAR_ITEMS_SHOWN]
    ]
    return "\n".join([SIMILAR_HEADING, *lines])
