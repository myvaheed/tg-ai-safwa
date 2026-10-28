"""What Edit offers on a Card proposal: the controls the Card's own screen has (CD-EDIT-067).

In the order that screen lists them, under its labels, with its choices and its words. A
Card's Goals and Checks are not offered, and neither is its kind. A proposal that moves,
finishes or reopens an Action offers its Stage as where Save leaves it, Done included
(CD-EDIT-068).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange, ChangeAction
from tg_agent_shell.foundation.references import ReferenceSpec
from tg_agent_shell.proposals.api import EditableField

from ....constants import SELECTOR_PAGE_SIZE
from ...schedules.telegram import REMIND_REVIEW_FIELD, schedule_review_field
from ..api import EFFORT_POINTS_ABOUT
from ..model import Card, CardKind, CardStage
from ..references import TAG_REFERENCE, VALUE_REFERENCE
from .effort_page import EFFORT_POINTS_TEXT
from .selectors import CHOICE_TITLES, choice_options
from .text_input import CARD_MARKDOWN_FIELDS, card_text_prompt, card_text_validator


def _typed(label: str, field: str, *, deadline: bool = False) -> EditableField:
    title, instruction = card_text_prompt(field, deadline=deadline)
    if field == "schedule":
        return schedule_review_field(label, title, instruction, card_text_validator(field))
    return EditableField(
        label,
        title,
        parse=card_text_validator(field),
        instruction=instruction,
        markdown=field in CARD_MARKDOWN_FIELDS,
    )


def _chosen(
    label: str, choice: str, *, many: bool = False, reference: ReferenceSpec | None = None
) -> EditableField:
    return EditableField(
        label,
        CHOICE_TITLES[choice],
        options=lambda session, _card_id: choice_options(session, choice),
        many=many,
        reference=reference,
        page_size=SELECTOR_PAGE_SIZE if reference is not None else None,
    )


def _finishes(call: AgentChange) -> bool:
    """Whether Save finishes the Action: then its time and Effort are offered though Safwa
    set neither (CD-DONE-072)."""
    return call.action is ChangeAction.COMPLETE


def _left_at(call: AgentChange) -> str:
    """Where Save leaves the Action: Done when the call finishes it, else its stage."""
    if call.action is ChangeAction.COMPLETE:
        return CardStage.DONE.value
    return str(call.values.get("stage", CardStage.BACKLOG.value))


def _leave_at(call: AgentChange, stage: str) -> AgentChange:
    """The call that leaves the Action at `stage`: a finish for Done, else a move there, or a
    reopen there when the call reopened it. A finish takes only the time it took and a move
    only the stage, so nothing else of the call stays but the Card's kind."""
    kind = {"kind": call.values["kind"]}
    if stage == CardStage.DONE.value:
        return call.model_copy(update={"action": ChangeAction.COMPLETE, "values": kind})
    action = ChangeAction.REOPEN if call.action is ChangeAction.REOPEN else ChangeAction.MOVE
    return call.model_copy(update={"action": action, "values": {**kind, "stage": stage}})


async def _other_stages(session: AsyncSession, card_id: int | None) -> list[tuple[str, Any]]:
    """Backlog, Sprint, Today and Done but the stage the Action is in, as its own screen
    offers its moves and Done."""
    card = await session.get(Card, card_id) if card_id is not None else None
    own = card.effective_stage if card is not None else None
    stages = (CardStage.BACKLOG, CardStage.SPRINT, CardStage.TODAY, CardStage.DONE)
    return [(stage.value.title(), stage.value) for stage in stages if stage.value != own]


_LINKS = {
    "values": _chosen("💎 Values", "values", many=True, reference=VALUE_REFERENCE),
    "tags": _chosen("🏷 Tags", "tags", many=True, reference=TAG_REFERENCE),
}
ACTION_FIELDS = {
    "title": _typed("✏️ Title", "title"),
    "note": _typed("📝 Note", "note"),
    "stage": _chosen("📍 Stage", "stage"),
    "priority": _chosen("⚠️ Priority", "priority"),
    "schedule": _typed("⏱ Schedule", "schedule"),
    "remind": REMIND_REVIEW_FIELD,
    "blocked_description": _typed("📝 Blocked reason", "blocked_description"),
    "effort_points": replace(
        _chosen("🔢 Effort", "effort"), about=(EFFORT_POINTS_ABOUT, EFFORT_POINTS_TEXT)
    ),
    "tracked_mins": _typed("⌛ Time spent", "tracked_mins"),
    "categories": _chosen("🏷 Categories", "categories", many=True),
    "energy_types": _chosen("⚡ Energy", "energy", many=True),
    **_LINKS,
}
# A move, a finish or a reopen: where Save leaves the Action, which may be another of them.
MOVE_FIELDS = {
    **ACTION_FIELDS,
    "stage": EditableField(
        "📍 Stage",
        CHOICE_TITLES["stage"],
        options=_other_stages,
        read=_left_at,
        write=_leave_at,
    ),
    "effort_points": replace(ACTION_FIELDS["effort_points"], offered=_finishes),
    "tracked_mins": replace(ACTION_FIELDS["tracked_mins"], offered=_finishes),
}
GOAL_FIELDS = {
    "title": _typed("✏️ Title", "title"),
    "note": _typed("📝 Note", "note"),
    "priority": _chosen("⚠️ Priority", "priority"),
    "schedule": _typed("⏰ Deadline", "schedule", deadline=True),
    "remind": REMIND_REVIEW_FIELD,
    **_LINKS,
}


def card_review_fields(call: AgentChange) -> Mapping[str, EditableField]:
    """Every Card call names the kind whose own controls it offers."""
    if call.values.get("kind") == CardKind.THOUGHT.value:
        return {} if call.action is ChangeAction.CREATE else {
            name: ACTION_FIELDS[name] for name in ("title", "note", "blocked_description")
        }
    if call.values.get("kind") != CardKind.ACTION.value:
        return GOAL_FIELDS
    if call.action in {ChangeAction.MOVE, ChangeAction.COMPLETE, ChangeAction.REOPEN}:
        return MOVE_FIELDS
    return ACTION_FIELDS
