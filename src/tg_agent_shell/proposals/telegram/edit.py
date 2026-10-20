"""Edit on a review screen: a field a proposal's call sets, set by hand (PR-EDIT-033).

Every screen here is drawn in place of the review and stays one, so words written over it
interrupt it and an unanswered one expires as any review does (PR-EDIT-035). Nothing is
written: the value goes into the proposal, prepared again as Safwa's own would be, and Save
is still the owner's (PR-EDIT-034).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiogram.enums import ChatAction
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.errors import DomainError
from ...foundation.kinds import MessageKind
from ...foundation.references import listed, resolve_references
from ...telegram import (
    CallbackContext,
    CallbackHandler,
    Place,
    Services,
    TextInputScreen,
    choice_rows,
    choice_screen,
    paginate,
    render_text_input,
)
from ...telegram.contributions import TextInputFlow
from ..api import EditableField
from ..model import ChangeAction, ChangeProposal, ProposalChange
from ..render import ACTION_VERBS
from .screens import render_proposal

EDIT_FLOW = "proposal_edit"


def _field(
    services: Services, payload: Mapping[str, Any]
) -> tuple[ChangeProposal, ProposalChange, EditableField]:
    """The proposal, the change and the field a press or a typed value is for."""
    proposal = services.root.reviews.proposal(int(payload["id"]))
    if proposal is None:
        raise DomainError("Proposal is no longer pending")
    wanted = (int(payload["change"]), str(payload["key"]))
    for index, key, field in services.root.proposals.edit_fields(proposal.changes):
        if (index, key) == wanted:
            return proposal, proposal.changes[index], field
    raise DomainError("This field cannot be edited here")


def _values(
    services: Services, change: ProposalChange, key: str, field: EditableField
) -> tuple[Any, Any]:
    """What the change gives the field now, and what Safwa's call gave it."""
    call = services.root.proposals.current(change)
    if call is None or change.source is None:
        return None, None
    return field.said(key, call), field.said(key, change.source)


async def _ticked(session: AsyncSession, field: EditableField, value: Any) -> set[Any]:
    """The options a list stands for: names and ids alike, where they name items."""
    if field.reference is None:
        return set(listed(value))
    spec = field.reference
    return (await resolve_references(session, spec, {spec.field: value})).ids


async def _offered(
    session: AsyncSession, field: EditableField, change: ProposalChange
) -> list[tuple[str, Any]]:
    """The field's options; a link offers the items not linked yet, an unlink those linked."""
    options = (
        list(await field.options(session, change.entity_id)) if field.options is not None else []
    )
    spec = field.reference
    if spec is None or change.action not in {ChangeAction.LINK, ChangeAction.UNLINK}:
        return options
    owner = spec.link_model.__table__.c[spec.owner_key]
    linked = set(
        await session.scalars(select(spec.link_column).where(owner == change.entity_id))
    )
    unlinking = change.action is ChangeAction.UNLINK
    return [option for option in options if (option[1] in linked) == unlinking]


def _title(field: EditableField, change: ProposalChange) -> str:
    if field.reference is not None and change.action in {ChangeAction.LINK, ChangeAction.UNLINK}:
        return f"{ACTION_VERBS[change.action]} {field.reference.label}s"
    return field.title


async def _on_review(context: CallbackContext) -> None:
    await render_proposal(context.message, context.services, context.payload["id"])


async def _on_edit(context: CallbackContext) -> None:
    await render_proposal(context.message, context.services, context.payload["id"], editing=True)


async def _typed(context: CallbackContext, *, back: Place) -> None:
    """The field's words to type, as its item's own editor asks for them."""
    services, payload = context.services, context.payload
    proposal, change, field = _field(services, payload)
    key = str(payload["key"])
    current, _proposed = _values(services, change, key, field)
    await render_text_input(
        context.message,
        services,
        screen=TextInputScreen(
            title=field.title,
            current_value="" if current is None else str(current),
            instruction=field.instruction,
            back=back,
            markdown=field.markdown,
            related_id=proposal.id,
            kind=MessageKind.APPROVAL,
        ),
        state={"flow": EDIT_FLOW, "id": proposal.id, "change": int(payload["change"]), "key": key},
    )


async def _on_field(context: CallbackContext) -> None:
    """One field, the way its item's own screen offers it."""
    services, payload = context.services, context.payload
    proposal, change, field = _field(services, payload)
    key = str(payload["key"])
    address = {"id": proposal.id, "change": int(payload["change"]), "key": key}
    edit = Place("proposal_edit", {"id": proposal.id})
    if field.options is None:
        await _typed(context, back=edit)
        return
    current, proposed = _values(services, change, key, field)
    here = Place("proposal_edit_field", address)
    async with context.sessions() as session:
        options = await _offered(session, field, change)
        if field.many:
            chosen = payload.get("chosen")
            ticked = set(chosen) if chosen is not None else await _ticked(session, field, current)
            # Ticked back to what Safwa proposed, the field keeps Safwa's own words.
            same = ticked == await _ticked(session, field, proposed)
            here = here.but(chosen=sorted(ticked))
            # Taps only move ticks, so the way back is what carries them into the proposal.
            back = Place(
                "proposal_edit_set", {**address, "value": proposed if same else sorted(ticked)}
            )
        else:
            ticked, back = {current}, edit
    # A single choice whose own screen also takes typed words offers them under Other.
    other = (
        [("✏️ Other", here.child("proposal_edit_other", **address))]
        if field.parse is not None and not field.many
        else []
    )
    page = (
        paginate(options, int(payload.get("page", 0)), field.page_size)
        if field.page_size is not None
        else None
    )
    if page is not None:
        here = here.but(page=page.index)

    def build(value: Any) -> Place:
        if field.many:
            return here.but(chosen=sorted(ticked ^ {value}))
        return Place("proposal_edit_set", {**address, "value": value})

    await choice_screen(
        context.message,
        services,
        _title(field, change),
        choice_rows(page.items if page is not None else options, ticked, build) + other,
        back=back,
        paging=(page, here) if page is not None else None,
        kind=MessageKind.APPROVAL,
        related_id=proposal.id,
    )


async def _on_other(context: CallbackContext) -> None:
    """Typed words for a field whose own screen takes them beside its choices."""
    edit = Place("proposal_edit", {"id": context.payload["id"]})
    await _typed(context, back=context.back or edit)


async def _typing(message: Message) -> None:
    """Preparing the proposal again may wait on a model, as Safwa's own call did."""
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)


async def _on_set(context: CallbackContext) -> None:
    payload = context.payload
    await _typing(context.message)
    async with context.sessions() as session:
        await context.services.root.edit_proposal(
            session,
            int(payload["id"]),
            int(payload["change"]),
            str(payload["key"]),
            payload["value"],
        )
    await render_proposal(context.message, context.services, payload["id"])


def _still_open(services: Services, state: Mapping[str, Any]) -> bool:
    return services.root.reviews.proposal(int(state["id"])) is not None


async def _take_typed(
    message: Any, services: Services, state: Mapping[str, Any], value: str
) -> Any:
    """Put the typed value into the proposal. It is prepared again before anything is
    drawn, and may wait on a model, so it runs outside the editor's transaction."""
    _proposal, _change, field = _field(services, state)
    parsed = field.parse(value) if field.parse is not None else value
    await _typing(message)
    async with services.sessions() as session:
        await services.root.edit_proposal(
            session, int(state["id"]), int(state["change"]), str(state["key"]), parsed
        )
    return parsed


async def _nothing_to_write(
    session: AsyncSession, services: Services, state: Mapping[str, Any], value: Any
) -> None:
    """The value already went into the proposal; a review writes nothing before Save."""
    del session, services, state, value


async def _back_to_review(
    message: Any, services: Services, state: Mapping[str, Any], value: Any
) -> None:
    del value
    await render_proposal(
        message,
        services,
        int(state["id"]),
        replace_message_id=int(state["text_input"]["message_id"]),
    )


EDIT_TEXT_INPUT = TextInputFlow(
    name=EDIT_FLOW,
    validator=lambda state: None,
    apply=_nothing_to_write,
    render=_back_to_review,
    prepare=_take_typed,
    open=_still_open,
)

EDIT_ACTIONS: dict[str, CallbackHandler] = {
    "proposal_review": _on_review,
    "proposal_edit": _on_edit,
    "proposal_edit_field": _on_field,
    "proposal_edit_other": _on_other,
    "proposal_edit_set": _on_set,
}
