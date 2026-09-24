"""How a proposed Card reads to the owner on its review screen."""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.foundation.references import resolve_references
from tg_agent_shell.proposals.api import (
    ChangeAction,
    ProposalChange,
    ProposalScreen,
)
from tg_agent_shell.proposals.render import (
    ACTION_VERBS,
    detail_label,
    detail_lines,
    detail_value,
    display_diff_value,
    reference_details,
    reference_groups,
    reference_names,
    result_value,
)

from ...tags.model import CardTag
from ...values.model import CardValue
from ..hard_time import hard_time_text, workspace_zone
from ..hierarchy import card_progress
from ..model import Card, CardCategory, CardEnergyType, CardKind, CardStage, Priority
from ..references import CARD_REFERENCE_SPECS
from .presentation import card_overview_text, category_expression, energy_expression

CARD_DETAIL_FIELDS = (
    "kind",
    "title",
    "note",
    "stage",
    "priority",
    "hard_time",
    "hard_time_description",
    "blocked",
    "blocked_description",
    "effort_points",
    "repeatable",
    "categories",
    "energy_types",
    "parent_id",
)

# The three fields a screen words shorter than the field name does.
CARD_LABELS = {
    "effort_points": "Effort",
    "energy_types": "Energy",
    "parent_id": "Parent ID",
}


# Every Card relationship diffs and renders through its spec, so a new one shows up here
# without a second table to update.
_REFERENCE_BY_PLURAL = {spec.plural_key: spec for spec in CARD_REFERENCE_SPECS}


def normalized_card_details(values: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    fields = {name: values[name] for name in CARD_DETAIL_FIELDS if name in values}
    if creating:
        fields.setdefault("stage", CardStage.BACKLOG.value)
        fields.setdefault("note", "")
        fields.setdefault("priority", "medium")
        fields.setdefault("hard_time", None)
        fields.setdefault("blocked", False)
        if fields.get("kind") == CardKind.ACTION.value:
            fields.setdefault("repeatable", False)
            fields.setdefault("categories", [])
            fields.setdefault("energy_types", [])
    if referenced_values := reference_details(values, "value"):
        fields["values"] = referenced_values
    if referenced_tags := reference_details(values, "tag"):
        fields["tags"] = referenced_tags
    if referenced_checks := reference_details(values, "check"):
        fields["checks"] = referenced_checks
    return fields


def _with_hard_time_text(fields: dict[str, Any], tz: ZoneInfo) -> dict[str, Any]:
    """A prepared Hard Time is a schedule payload; the owner reads it in words."""
    if isinstance(fields.get("hard_time"), dict):
        fields["hard_time"] = hard_time_text(fields["hard_time"], tz=tz)
    return fields


async def _card_detail_snapshot(session: AsyncSession, card: Card) -> dict[str, Any]:
    return {
        "kind": card.kind,
        "title": card.title,
        "note": card.note,
        "stage": card.effective_stage,
        "priority": card.priority,
        "hard_time": hard_time_text(card.hard_time, tz=await workspace_zone(session)),
        "hard_time_description": card.hard_time_description,
        "blocked": card.blocked,
        "blocked_description": card.blocked_description,
        "effort_points": card.effort_points,
        "repeatable": card.repeatable,
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
        "parent_id": card.parent_id,
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
    tz = await workspace_zone(session)
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
                "hard_time": hard_time_text(card.hard_time, tz=tz),
                "hard_time_description": card.hard_time_description,
                "blocked": card.blocked,
                "blocked_description": card.blocked_description,
                "effort_points": card.effort_points,
                "repeatable": card.repeatable,
                "parent_id": card.parent_id,
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
                current[spec.plural_key] = sorted(
                    await session.scalars(
                        select(spec.link_column).where(spec.link_model.card_id == card.id)
                    )
                )
    states = [current]
    unresolved_references: list[tuple[str, str]] = []
    for change in changes:
        before = states[-1]
        proposed = _with_hard_time_text({**before, **dict(change.values)}, tz)
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
            existing = set(before.get(spec.plural_key, []))
            if change.action is ChangeAction.LINK:
                proposed[spec.plural_key] = sorted(existing | target_ids)
            elif change.action is ChangeAction.UNLINK:
                proposed[spec.plural_key] = sorted(existing - target_ids)
            else:
                proposed[spec.plural_key] = sorted(target_ids)
        states.append(proposed)
    return states, unresolved_references


async def _card_display_state(
    session: AsyncSession, state: dict[str, Any]
) -> dict[str, Any]:
    display = dict(state)
    parent = await session.get(Card, state.get("parent_id")) if state.get("parent_id") else None
    display["parent_name"] = parent.title if parent else None
    for spec in CARD_REFERENCE_SPECS:
        display[spec.plural_key.replace("_ids", "_names")] = await reference_names(
            session, spec, state.get(spec.plural_key)
        )
    if display.get("id") and display.get("kind") in {
        CardKind.GOAL.value,
        CardKind.SUBGOAL.value,
    }:
        display.update(await card_progress(session, int(display["id"])))
    return display


async def _card_diff_value(session: AsyncSession, field: str, value: Any) -> str:
    if field == "parent_id":
        if value is None:
            return "Root"
        parent = await session.get(Card, value)
        return parent.title if parent else f"Card #{value}"
    spec = _REFERENCE_BY_PLURAL.get(field)
    if spec is not None:
        return ", ".join(await reference_names(session, spec, value)) or "—"
    if field == "categories":
        return category_expression(value)
    if field == "energy_types":
        return energy_expression(value)
    if field in {"kind", "stage", "priority"} and value:
        return str(value).title()
    return display_diff_value(value)


async def _card_diffs(
    session: AsyncSession, current: dict[str, Any], proposed: dict[str, Any]
) -> list[str]:
    labels = {
        "kind": "Kind",
        "title": "Title",
        "note": "Note",
        "parent_id": "Parent",
        "stage": "Stage",
        "priority": "Priority",
        "hard_time": "Hard Time",
        "hard_time_description": "Hard Time description",
        "blocked": "Blocked",
        "blocked_description": "Blocked Description",
        "effort_points": "Effort",
        "repeatable": "Repeatable",
        "categories": "Categories",
        "energy_types": "Energy",
        **{spec.plural_key: f"{spec.label}s" for spec in CARD_REFERENCE_SPECS},
        "status": "Status",
    }
    diffs: list[str] = []
    for field, label in labels.items():
        if current.get(field) == proposed.get(field):
            continue
        old = await _card_diff_value(session, field, current.get(field))
        new = await _card_diff_value(session, field, proposed.get(field))
        diffs.append(f"• {label}: {html.escape(old)} → {html.escape(new)}")
    return diffs


class CardProposalPresenter:
    entity = "card"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(
            normalized_card_details(
                dict(change.values), creating=change.action is ChangeAction.CREATE
            ),
            CARD_LABELS,
        )

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        values = dict(change.values)
        tz = await workspace_zone(session)
        if change.action is ChangeAction.CREATE:
            return detail_lines(
                _with_hard_time_text(normalized_card_details(values, creating=True), tz),
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
        proposed = _with_hard_time_text(normalized_card_details(values, creating=False), tz)
        if change.action is ChangeAction.MOVE:
            proposed = {"stage": values.get("stage")}
        elif change.action is ChangeAction.COMPLETE:
            proposed = {"stage": CardStage.DONE.value}
        elif change.action is ChangeAction.REOPEN:
            proposed = {"stage": values.get("stage", CardStage.BACKLOG.value)}
        elif change.action in {ChangeAction.ARCHIVE, ChangeAction.DELETE}:
            return [f"Card: {card.kind.title()} #{card.id} “{card.title}”"]
        return [
            f"{detail_label(field, CARD_LABELS)}: {detail_value(before.get(field))} → {detail_value(value)}"
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
        title = result_value(values.get("title") or (card.title if card else ""))
        kind = str(values.get("kind") or (card.kind if card else "") or "card")
        head = f"{kind.title()} “{title}”" if title else f"Card #{change.entity_id}"
        parent = (
            await session.get(Card, int(values["parent_id"]))
            if values.get("parent_id")
            else None
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
            if values.get("hard_time"):
                parts.append(
                    f"Hard Time {hard_time_text(values['hard_time'], tz=await workspace_zone(session))}"
                )
            if values.get("repeatable"):
                parts.append("Repeatable")
            if values.get("blocked"):
                parts.append("Blocked")
            parts.extend(await reference_groups(session, values, CARD_REFERENCE_SPECS))
        elif action in {ChangeAction.MOVE, ChangeAction.REOPEN} and values.get("stage"):
            parts.append(str(values["stage"]).title())
        elif action is ChangeAction.UPDATE:
            parts.extend(detail for detail in details if not detail.startswith("Parent ID:"))
        if parent is not None:
            head += f" under {parent.kind.title()} “{result_value(parent.title)}”"
        return f"{verb} {head}" + (f" ({' · '.join(parts)})" if parts else "")

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        states, unresolved = await _card_states(session, changes)
        display = await _card_display_state(session, states[-1])
        creating = changes[0].action is ChangeAction.CREATE
        # The Card screen is the overview itself; a field diff repeats it only when the
        # Card already exists, and then there is one for each change, in the order of Save.
        diffs: list[str] = []
        if not creating:
            for before, after in zip(states, states[1:], strict=False):
                diffs.extend(await _card_diffs(session, before, after))
            diffs.extend(
                f"• {label}: — → {html.escape(name)} (not found)" for label, name in unresolved
            )
        return ProposalScreen(
            mode="Create" if creating else "Edit",
            item="Card",
            blocks=(card_overview_text(display, heading="Card overview"),),
            diffs=tuple(diffs),
        )
