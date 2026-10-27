"""How a Check reads to the owner: its review screen, and the screen a citation opens."""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.foundation.references import resolve_references
from tg_agent_shell.proposals.api import (
    ChangeAction,
    ProposalChange,
    ProposalScreen,
    ShownField,
)
from tg_agent_shell.proposals.marks import shown
from tg_agent_shell.proposals.render import (
    detail_label,
    detail_lines,
    detail_value,
    named_summary,
    reference_groups,
    reference_names,
    result_value,
)
from tg_agent_shell.telegram import citation_title

from ....foundation.marks import title_marks
from ...schedules.api import entity_reminder, remind_label, rule_summary, workspace_zone
from ..model import CHECK_OUTCOME_LABELS, Check
from ..proposal import CHECK_ANSWER_ACTIONS
from ..references import CHECK_VALUE_REFERENCE
from ..use_cases import check_value_ids


async def _value_names(session: AsyncSession, value_ids: list[int]) -> list[str]:
    """The Values on a Check, as the owner reads them on a screen."""
    names = await reference_names(session, CHECK_VALUE_REFERENCE, sorted(value_ids))
    return [result_value(name) for name in names]


# A Check's answer reads as its status, not as the column it is stored in.
CHECK_LABELS = {"outcome": "Status"}


class CheckProposalPresenter:
    entity = "check"

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(dict(change.values), CHECK_LABELS)

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        values = dict(change.values)
        if change.action in {ChangeAction.LINK, ChangeAction.UNLINK}:
            verb = change.action.title()
            groups = await reference_groups(session, values, (CHECK_VALUE_REFERENCE,))
            return [f"{verb}: {group}" for group in groups]
        proposed = {
            name: values[name] for name in ("title", "note", "schedule") if name in values
        }
        if "remind" in values:
            proposed["remind"] = remind_label(values["remind"])
        if change.action in CHECK_ANSWER_ACTIONS:
            proposed["outcome"] = CHECK_OUTCOME_LABELS[CHECK_ANSWER_ACTIONS[change.action]]
            if (values.get("times") or 1) > 1:
                proposed["times"] = values["times"]
        check = (
            await session.get(Check, change.entity_id)
            if change.entity_id is not None
            else None
        )
        if change.action is ChangeAction.CREATE or check is None:
            if values.get(CHECK_VALUE_REFERENCE.field):
                resolved = await resolve_references(session, CHECK_VALUE_REFERENCE, values)
                proposed["values"] = ", ".join(await _value_names(session, sorted(resolved.ids)))
            return detail_lines(proposed, CHECK_LABELS)
        if change.action is ChangeAction.ARCHIVE:
            return [f"Check: #{check.id} “{result_value(check.title)}”"]
        reminder = await entity_reminder(session, check)
        before = {
            "title": check.title,
            "note": check.note,
            "schedule": check.schedule,
            "remind": remind_label(reminder.lead_minutes if reminder else None),
            "outcome": CHECK_OUTCOME_LABELS[check.status],
        }
        return [
            f"{detail_label(field, CHECK_LABELS)}: {detail_value(before.get(field))} → {detail_value(value)}"
            for field, value in proposed.items()
            if before.get(field) != value
        ]

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        if change.action in CHECK_ANSWER_ACTIONS:
            check = (
                await session.get(Check, change.entity_id)
                if change.entity_id is not None
                else None
            )
            outcome = CHECK_ANSWER_ACTIONS[change.action]
            head = f"“{result_value(check.title)}”" if check else f"#{change.entity_id}"
            times = change.values.get("times") or 1
            return f"Answer Check {head} ({CHECK_OUTCOME_LABELS[outcome]}" + (
                f" ×{times})" if times > 1 else ")"
            )
        return await named_summary(session, change, details, model=Check)

    async def screen(
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        first = changes[0]
        current: dict[str, Any] = {}
        linked: set[int] = set()
        if first.entity_id:
            check = await session.get(Check, first.entity_id)
            if check is not None:
                linked = set(await check_value_ids(session, check.id))
                reminder = await entity_reminder(session, check)
                current = {
                    "title": check.title,
                    "note": check.note,
                    "schedule": check.schedule,
                    "remind": remind_label(reminder.lead_minutes) if reminder else None,
                    "status": CHECK_OUTCOME_LABELS[check.status],
                    "values": await _value_names(session, list(linked)),
                }
        proposed = dict(current)
        rule = None
        for change in changes:
            proposed.update(
                (k, v)
                for k, v in change.values.items()
                if k not in {CHECK_VALUE_REFERENCE.field, "schedule_rule", "remind"}
            )
            if "remind" in change.values:
                lead = change.values["remind"]
                proposed["remind"] = None if lead is None else remind_label(lead)
            if "schedule" in change.values:
                rule = change.values.get("schedule_rule")
            if change.action in {ChangeAction.LINK, ChangeAction.UNLINK} or (
                change.action is ChangeAction.CREATE and CHECK_VALUE_REFERENCE.field in change.values
            ):
                resolved = await resolve_references(
                    session, CHECK_VALUE_REFERENCE, change.values
                )
                target = resolved.ids | set(resolved.unknown_ids)
                linked = linked - target if change.action is ChangeAction.UNLINK else linked | target
                proposed["values"] = await _value_names(session, list(linked))
            if change.action in CHECK_ANSWER_ACTIONS:
                proposed["status"] = CHECK_OUTCOME_LABELS[CHECK_ANSWER_ACTIONS[change.action]]
        answering = any(change.action in CHECK_ANSWER_ACTIONS for change in changes)
        return ProposalScreen(
            item="Check",
            fields=tuple(
                ShownField(
                    label, shown(current.get(key)), shown(proposed.get(key)), anchor=key == "title"
                )
                for key, label in (
                    ("title", "Title"),
                    ("note", "Note"),
                    ("status", "Status"),
                    ("schedule", "Schedule"),
                    ("remind", "Remind"),
                    ("values", "Values"),
                )
            ),
            blocks=(html.escape(rule_summary(rule, await workspace_zone(session))),)
            if rule
            else (),
            mode="Answer" if answering else None,
        )


async def check_citation_label(
    session: AsyncSession, services: Any, check: Check, *, short: bool
) -> str | None:
    """A live Check keeps the model's own words.

    A closed repeat or an archived one has to carry its marks, or the link looks exactly
    like the open one it was superseded by.
    """
    marker = await title_marks(session, check)
    return f"{citation_title(check.title, short=short)}{marker}" if marker else None
