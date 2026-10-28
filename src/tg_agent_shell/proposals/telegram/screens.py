"""The review screen: what the model proposed, behind Save and Discard, Edit when the
owner may set some of it by hand, and what it may turn into instead."""

from __future__ import annotations

import html
from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete
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
from ...telegram.model import UiSession
from ..api import (
    EditableField,
    Effect,
    ProposalAction,
    ProposalScreen,
    ProposalSwitch,
    ShownField,
    effect_of,
)
from ..marks import field_line, shown
from ..model import ChangeAction, ProposalChange
from ..render import detail_label, result_value

SIMILAR_HEADING = "<b>⚠️ Similar items already exist</b>"
EDITED_BY_YOU = "✍️ Edited by you"
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
    editing: bool = False,
) -> None:
    """Draw one review. `editing` puts the fields Edit offers in place of its decision."""
    proposal = services.root.reviews.proposal(proposal_id)
    if proposal is None:
        raise DomainError("Proposal is no longer pending")
    async with services.sessions() as session:
        changes = proposal.changes
        fields = services.root.proposals.edit_fields(changes)
        # A proposal changes one item, so the feature that owns it draws the screen; one
        # that draws none gets each value it sets, which knows nothing about the entity.
        presenter = services.root.proposals.presenter(changes[0].entity)
        screen = await presenter.screen(session, changes) if presenter is not None else None
        screen = screen or _plain_screen(changes)
        effect = effect_of(changes)
        heading = (
            f"<b>{EFFECT_ICONS[effect]} {html.escape(screen.mode or effect.value)} "
            f"{html.escape(screen.item)}</b>"
        )
        text_parts = [f"{heading} · {proposal.place}" if proposal.place else heading]
        edited = [field.label for index, key, field in fields if key in changes[index].edits]
        if edited:
            text_parts.append(f"<i>{EDITED_BY_YOU}:</i> {html.escape(', '.join(edited))}")
        if notice:
            text_parts.append(html.escape(notice))
        text_parts.append(await similar_items(session, services, changes[0]))
        text_parts.append(
            "\n".join(line for field in screen.fields if (line := field_line(field, effect)))
        )
        text_parts.extend(screen.blocks)
        # Drawn again, the review takes the place of any field that stood in for it. Written
        # only after the similar items are read, which the search index refreshes for.
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        rows = (
            await _edit_rows(session, services, proposal.id, fields)
            if editing
            else await _decision_rows(
                session,
                services,
                proposal.id,
                effect,
                fields,
                services.root.proposals.offered_switches(changes),
                services.root.proposals.offered_actions(changes),
            )
        )
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


async def _decision_rows(
    session: AsyncSession,
    services: Services,
    proposal_id: int,
    effect: Effect,
    fields: list[tuple[int, str, EditableField]],
    switches: list[tuple[int, ProposalSwitch]],
    actions: Sequence[Sequence[ProposalAction]] = (),
) -> list[list[InlineKeyboardButton]]:
    """Save, named for what it does to the item, and Discard; Edit above them when there is
    something to edit (PR-EDIT-033), and above Edit what the proposal may turn into
    (PR-SWITCH-037)."""
    payload = {"id": proposal_id}
    turns = [
        [
            await token_button(
                session,
                services.owner_id,
                switch.label,
                "proposal_switch",
                {"id": proposal_id, "switch": place},
            )
        ]
        for place, switch in switches
    ]
    edit = (
        [[await token_button(session, services.owner_id, "✏️ Edit", "proposal_edit", payload)]]
        if fields
        else []
    )
    return [
        *[
            [await token_button(
                session, services.owner_id, button.label, button.action,
                {**button.payload, "id": proposal_id},
            ) for button in row]
            for row in actions
        ],
        *turns,
        *edit,
        [
            await token_button(
                session, services.owner_id, f"✅ {effect.value}", "proposal_approve", payload
            ),
            await token_button(
                session, services.owner_id, "🗑 Discard", "proposal_reject", payload
            ),
        ],
    ]


async def _edit_rows(
    session: AsyncSession,
    services: Services,
    proposal_id: int,
    fields: list[tuple[int, str, EditableField]],
) -> list[list[InlineKeyboardButton]]:
    """Each field Edit offers, two to a row as on an item's own screen, then the way back."""
    buttons = [
        await token_button(
            session,
            services.owner_id,
            field.label,
            "proposal_edit_field",
            {"id": proposal_id, "change": index, "key": key},
        )
        for index, key, field in fields
    ]
    back = await token_button(
        session, services.owner_id, "↩️ Back", "proposal_review", {"id": proposal_id}
    )
    return [*(buttons[start : start + 2] for start in range(0, len(buttons), 2)), [back]]


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
