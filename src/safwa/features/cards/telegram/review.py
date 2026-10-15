"""How a proposed Card reads to the owner on its review screen."""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.foundation.references import resolve_references
from tg_agent_shell.proposals.api import (
    ChangeAction,
    FieldValue,
    ProposalChange,
    ProposalScreen,
    ShownField,
)
from tg_agent_shell.proposals.marks import shown
from tg_agent_shell.proposals.render import (
    ACTION_VERBS,
    became,
    detail_label,
    detail_lines,
    detail_value,
    reference_details,
    reference_groups,
    reference_names,
    result_value,
)

from ...schedules.api import rule_summary, workspace_zone
from ...tags.model import CardTag
from ...values.model import CardValue
from ..api import parents_of, tree_level
from ..model import (
    Card,
    CardCategory,
    CardEnergyType,
    CardKind,
    CardStage,
    Priority,
    card_kind_name,
    minutes_label,
)
from ..references import CARD_REFERENCE_SPECS, PARENT_REFERENCE
from .presentation import category_expression, energy_expression, kind_label

CARD_DETAIL_FIELDS = (
    "kind",
    "title",
    "note",
    "stage",
    "priority",
    "schedule",
    "blocked_description",
    "effort_points",
    "tracked_mins",
    "categories",
    "energy_types",
)

# The fields a screen words otherwise than the field name does.
CARD_LABELS = {
    "effort_points": "Effort",
    "tracked_mins": "Time spent",
    "energy_types": "Energy",
}


# Every Card relationship diffs and renders through its spec, so a new one shows up here
# without a second table to update.
_REFERENCE_BY_FIELD = {spec.field: spec for spec in CARD_REFERENCE_SPECS}


def normalized_card_details(values: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    fields = {name: values[name] for name in CARD_DETAIL_FIELDS if name in values}
    if creating:
        fields.setdefault("stage", CardStage.BACKLOG.value)
        fields.setdefault("note", "")
        fields.setdefault("priority", "medium")
        fields.setdefault("schedule", None)
        if fields.get("kind") == CardKind.ACTION.value:
            fields.setdefault("categories", [])
            fields.setdefault("energy_types", [])
    if "kind" in fields:
        fields["kind"] = card_kind_name(fields["kind"], bool(values.get(PARENT_REFERENCE.field))).lower()
    for spec in CARD_REFERENCE_SPECS:
        if referenced := reference_details(values, spec):
            fields[spec.field] = referenced
    return fields


def _detail(field: str, value: Any) -> str:
    """One side of a detail line; the time reads in hours and minutes."""
    if field == "tracked_mins" and value:
        return minutes_label(value)
    return detail_value(value)


async def _card_detail_snapshot(session: AsyncSession, card: Card) -> dict[str, Any]:
    parents = (await parents_of(session, [card.id])).get(card.id, [])
    return {
        "kind": card_kind_name(card.kind, bool(parents)).lower(),
        "title": card.title,
        "note": card.note,
        "stage": card.effective_stage,
        "priority": card.priority,
        "schedule": card.schedule,
        "blocked_description": card.blocked_description,
        "effort_points": card.effort_points,
        "tracked_mins": card.tracked_mins,
        "categories": sorted(
            await session.scalars(
                select(CardCategory.category).where(CardCategory.card_id == card.id)
            )
        ),
        "energy_types": sorted(
            await session.scalars(
                select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
            )
        ),
        "parents": [f"#{item}" for item in parents],
        "values": [
            f"#{item}"
            for item in sorted(
                await session.scalars(
                    select(CardValue.value_id).where(CardValue.card_id == card.id)
                )
            )
        ],
        "tags": [
            f"#{item}"
            for item in sorted(
                await session.scalars(select(CardTag.tag_id).where(CardTag.card_id == card.id))
            )
        ],
    }


async def _card_states(
    session: AsyncSession, changes: Sequence[ProposalChange]
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """The Card as it is, then as each change leaves it, and the names none of them found."""
    current: dict[str, Any] = {}
    if changes[0].entity_id:
        card = await session.get(Card, changes[0].entity_id)
        if card is not None:
            current = {
                "id": card.id,
                "kind": card.kind,
                "title": card.title,
                "note": card.note,
                "stage": card.effective_stage,
                "priority": card.priority,
                "schedule": card.schedule,
                "blocked_description": card.blocked_description,
                "effort_points": card.effort_points,
                "tracked_mins": card.tracked_mins,
                "categories": sorted(
                    await session.scalars(
                        select(CardCategory.category).where(CardCategory.card_id == card.id)
                    )
                ),
                "energy_types": sorted(
                    await session.scalars(
                        select(CardEnergyType.energy_type).where(
                            CardEnergyType.card_id == card.id
                        )
                    )
                ),
                # Only an archive or a delete moves it, so only those show it in a diff.
                "status": "Archived" if card.archived_at is not None else "Active",
            }
            for spec in CARD_REFERENCE_SPECS:
                current[spec.field] = sorted(
                    await session.scalars(
                        select(spec.link_column).where(spec.link_model.card_id == card.id)
                    )
                )
    states = [current]
    unresolved_references: list[tuple[str, str]] = []
    for change in changes:
        before = states[-1]
        proposed = {**before, **dict(change.values)}
        if change.action is ChangeAction.COMPLETE:
            proposed["stage"] = CardStage.DONE.value
        elif change.action is ChangeAction.REOPEN:
            proposed["stage"] = change.values.get("stage", CardStage.BACKLOG.value)
        elif change.action in {ChangeAction.ARCHIVE, ChangeAction.DELETE}:
            proposed["status"] = (
                "Archived" if change.action is ChangeAction.ARCHIVE else "Deleted"
            )
        for spec in CARD_REFERENCE_SPECS:
            if not spec.mentioned_in(change.values):
                continue
            resolved = await resolve_references(session, spec, change.values)
            target_ids = resolved.ids | set(resolved.unknown_ids)
            unresolved_references.extend((spec.label, name) for name in resolved.unresolved)
            existing = set(before.get(spec.field, []))
            if change.action is ChangeAction.LINK:
                proposed[spec.field] = sorted(existing | target_ids)
            elif change.action is ChangeAction.UNLINK:
                proposed[spec.field] = sorted(existing - target_ids)
            else:
                proposed[spec.field] = sorted(target_ids)
        states.append(proposed)
    return states, unresolved_references


async def _card_shown(
    session: AsyncSession, field: str, state: dict[str, Any]
) -> FieldValue:
    """One field of a Card state in the words its screen shows it with."""
    value = state.get(field)
    if field == "kind":
        level = await tree_level(session, value, state.get("id"))
        return kind_label(value, bool(state.get(PARENT_REFERENCE.field)), level=level) if value else None
    spec = _REFERENCE_BY_FIELD.get(field)
    if spec is not None:
        return shown(await reference_names(session, spec, value))
    if field in {"categories", "energy_types"}:
        expression = category_expression if field == "categories" else energy_expression
        return tuple(expression([item]) for item in value or ())
    if field == "tracked_mins":
        return minutes_label(value) if value else None
    if field in {"stage", "priority"} and value:
        return str(value).title()
    return shown(value)


async def _card_fields(
    session: AsyncSession,
    before: dict[str, Any],
    after: dict[str, Any],
    unresolved: list[tuple[str, str]],
) -> tuple[ShownField, ...]:
    """The Card before its changes and after them, in the order its own screen lists it."""
    action = (after.get("kind") or before.get("kind")) == CardKind.ACTION.value
    labels = {
        "kind": "Kind",
        "title": "Title",
        PARENT_REFERENCE.field: "Parents",
        "stage": "Stage",
        "note": "Note",
        "schedule": "Schedule" if action else "Deadline",
        "priority": "Priority",
        "blocked_description": "Blocked",
        "effort_points": "Effort",
        "tracked_mins": "Time spent",
        "categories": "Categories",
        "energy_types": "Energy",
        **{spec.field: f"{spec.label}s" for spec in CARD_REFERENCE_SPECS},
    }
    # A new Card's defaults say nothing the owner chose.
    defaults = {"stage": CardStage.BACKLOG.value, "priority": Priority.MEDIUM.value}
    missing = {
        spec.field: tuple(
            f"{name} (not found)" for label, name in unresolved if label == spec.label
        )
        for spec in CARD_REFERENCE_SPECS
    }
    # A Goal placed under another reads as a Subgoal, but no proposal changes its kind.
    kind = await _card_shown(session, "kind", after)
    fields = [ShownField(labels.pop("kind"), kind, kind, anchor=True)]
    for field, label in labels.items():
        chosen = bool(before) or after.get(field) != defaults.get(field)
        new = await _card_shown(session, field, after) if chosen else None
        if missing.get(field):
            new = (*(new or ()), *missing[field])
        fields.append(
            ShownField(
                label,
                await _card_shown(session, field, before),
                new,
                anchor=field in {"title", PARENT_REFERENCE.field},
            )
        )
    return tuple(fields)


class CardProposalPresenter:
    entity = "card"

    def raw_details(self, change: AgentChange) -> list[str]:
        values = dict(change.values)
        creating = change.action is ChangeAction.CREATE
        if not creating:
            # The kind a tool call addresses, not a change to it.
            values.pop("kind", None)
        return detail_lines(normalized_card_details(values, creating=creating), CARD_LABELS)

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        values = dict(change.values)
        if change.action is ChangeAction.CREATE:
            return detail_lines(
                normalized_card_details(values, creating=True),
                CARD_LABELS,
            )
        card = (
            await session.get(Card, change.entity_id)
            if change.entity_id is not None
            else None
        )
        if card is None:
            fallback_lines = self.raw_details(fallback) if fallback is not None else []
            return fallback_lines or detail_lines(
                normalized_card_details(values, creating=False), CARD_LABELS
            )
        before = await _card_detail_snapshot(session, card)
        if change.action in {ChangeAction.LINK, ChangeAction.UNLINK}:
            relationship = normalized_card_details(values, creating=False)
            verb = "Link" if change.action is ChangeAction.LINK else "Unlink"
            return [
                f"{verb} {detail_label(field, CARD_LABELS)}: {detail_value(value)}"
                for field, value in relationship.items()
            ]
        proposed = normalized_card_details(values, creating=False)
        if change.action is ChangeAction.MOVE:
            proposed = {"stage": values.get("stage")}
        elif change.action is ChangeAction.COMPLETE:
            proposed = {"stage": CardStage.DONE.value}
            if "tracked_mins" in values:
                proposed["tracked_mins"] = values["tracked_mins"]
        elif change.action is ChangeAction.REOPEN:
            proposed = {"stage": values.get("stage", CardStage.BACKLOG.value)}
        elif change.action in {ChangeAction.ARCHIVE, ChangeAction.DELETE}:
            name = card_kind_name(card.kind, bool(await parents_of(session, [card.id])))
            return [f"Card: {name} #{card.id} “{card.title}”"]
        return [
            f"{detail_label(field, CARD_LABELS)}: "
            f"{_detail(field, before.get(field))} → {_detail(field, value)}"
            for field, value in proposed.items()
            if before.get(field) != value
        ]

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        values = dict(change.values)
        action = change.action
        verb = ACTION_VERBS.get(action, action.title())
        card = (
            await session.get(Card, change.entity_id)
            if change.entity_id is not None
            else None
        )
        # An edit names the Card as it is called now; what it becomes is in its parts.
        title = result_value(card.title if card else values.get("title") or "")
        kind = str(values.get("kind") or (card.kind if card else "") or "card")
        has_parent = (
            bool(await parents_of(session, [card.id]))
            if card is not None
            else PARENT_REFERENCE.mentioned_in(values)
        )
        head = f"{card_kind_name(kind, has_parent)} “{title}”" if title else f"Card #{change.entity_id}"
        # A new Card names the Goal it is proposed under; a link names it as a link does.
        parents = (
            [
                parent
                for parent_id in sorted(
                    (await resolve_references(session, PARENT_REFERENCE, values)).ids
                )
                if (parent := await session.get(Card, parent_id)) is not None
            ]
            if action is ChangeAction.CREATE
            else []
        )
        if action in {ChangeAction.LINK, ChangeAction.UNLINK}:
            joined = " · ".join(
                await reference_groups(session, values, CARD_REFERENCE_SPECS)
            )
            preposition = "to" if action is ChangeAction.LINK else "from"
            return f"{verb} {joined} {preposition} {head}" if joined else f"{verb} {head}"
        parts: list[str] = []
        if action is ChangeAction.CREATE:
            # Only what was actually chosen: the defaults a new Card lands on say
            # nothing, and a receipt naming them buries the fields that do.
            stage = str(values.get("stage") or CardStage.BACKLOG.value)
            if stage != CardStage.BACKLOG.value:
                parts.append(stage.title())
            priority = str(values.get("priority") or Priority.MEDIUM.value)
            if priority != Priority.MEDIUM.value:
                parts.append(priority.title())
            if values.get("effort_points"):
                parts.append(f"{values['effort_points']} EP")
            for field_name in ("categories", "energy_types"):
                if chosen := values.get(field_name):
                    parts.append(detail_value(chosen))
            if values.get("schedule"):
                label = "Schedule" if kind == CardKind.ACTION.value else "Deadline"
                parts.append(f"{label} {values['schedule']}")
            if values.get("blocked_description"):
                parts.append("Blocked")
            others = tuple(spec for spec in CARD_REFERENCE_SPECS if spec is not PARENT_REFERENCE)
            parts.extend(await reference_groups(session, values, others))
        elif action in {ChangeAction.MOVE, ChangeAction.REOPEN} and values.get("stage"):
            parts.append(str(values["stage"]).title())
        elif action is ChangeAction.COMPLETE and values.get("tracked_mins"):
            parts.append(f"{minutes_label(values['tracked_mins'])} spent")
        elif action is ChangeAction.UPDATE:
            parts.extend(became(detail) for detail in details)
        if parents:
            above = await parents_of(session, [parent.id for parent in parents])
            head += " under " + ", ".join(
                f"{card_kind_name(parent.kind, parent.id in above)} “{result_value(parent.title)}”"
                for parent in parents
            )
        return f"{verb} {head}" + (f" ({' · '.join(parts)})" if parts else "")

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        states, unresolved = await _card_states(session, changes)
        after = states[-1]
        reading = (
            rule_summary(after["schedule_rule"], await workspace_zone(session))
            if after.get("schedule_rule")
            else None
        )
        return ProposalScreen(
            item=card_kind_name(after.get("kind") or "card", bool(after.get(PARENT_REFERENCE.field))),
            fields=await _card_fields(session, states[0], after, unresolved),
            blocks=(html.escape(reading),) if reading else (),
        )
