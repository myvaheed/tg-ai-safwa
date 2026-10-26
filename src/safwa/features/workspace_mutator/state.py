"""The workspace's current state, as one block a session reads before its own steps.

The workspace is what the owner keeps - Cards, Checks, Values, Tags, Requests and Reminders -
so the block that describes it is the workspace's, not the reader's. `AgentSpec.workspace_state`
is the flag that asks for it.

A running Sprint is read tactically: Today, the rest of the Sprint and the Goals that work
serves. Planning is read strategically: what serves each Value, and the Goals and Backlog
Actions to take next. Every list of Cards is in the focus order, cut to `CONTEXT_LIST_LIMIT`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.messages import StateBlocks
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.telegram.manifest import AgentContext

from ...foundation.closeness import closeness_to, closest
from ...foundation.workspace import Workspace
from ..cards.api import (
    Card,
    CardStage,
    actions_on_stages,
    order_cards,
    parents_of,
    served_values,
    tree_level,
)
from ..cards.model import CardCheck, CardKind, Priority, effort_label
from ..checks.model import Check
from ..planning.api import (
    capacity_effort_points,
    key_action_ids,
    plan_load,
    sprint_day,
    sprint_length_days,
    today_actions,
)
from ..planning.model import Sprint
from ..profile.model import UserProfile
from ..tags.model import Tag
from ..values.model import CheckValue, Value

CONTEXT_LIST_LIMIT = 10
# Every turn carries each Value's and Tag's description, so each is one short line.
CONTEXT_DESCRIPTION_MAX_CHARS = 120


def citation(name: str, kind: str, item_id: int) -> str:
    """The one shape an item takes in context, ready for the model to reuse in a reply."""
    return f"[{name}]({kind}:{item_id})"


def described(name: str, kind: str, item_id: int, description: str, fields: str = "") -> str:
    """A listed item and its description on one line, cut to `CONTEXT_DESCRIPTION_MAX_CHARS`."""
    about = " ".join(description.split())
    if len(about) > CONTEXT_DESCRIPTION_MAX_CHARS:
        about = about[: CONTEXT_DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"
    return (
        f"- {citation(name, kind, item_id)}"
        + (f" {fields}" if fields else "")
        + (f": {about}" if about else "")
    )


def counted(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def section(heading: str, items: list[str]) -> list[str]:
    # An empty heading would read the owner's next line as its first item.
    return [heading, *items] if items else []


async def workspace_context(session: AsyncSession) -> StateBlocks:
    workspace = await session.get(Workspace, 1)
    profile = await session.get(UserProfile, 1)
    values = list(await session.scalars(select(Value).order_by(Value.order)))
    tags = list(
        await session.scalars(select(Tag).order_by(Tag.name))
    )
    sprint = (
        await session.get(Sprint, workspace.active_sprint_id)
        if workspace and workspace.active_sprint_id
        else None
    )
    timezone = ZoneInfo(workspace.timezone if workspace else "Europe/Istanbul")
    local_now = utcnow().astimezone(timezone)
    local_day = local_now.date()
    effort_on = bool(profile and profile.effort_tracking)
    open_cards = list(
        await session.scalars(
            select(Card).where(
                Card.archived_at.is_(None), Card.effective_stage != CardStage.DONE.value
            )
        )
    )
    serves = await served_values(session, [card.id for card in open_cards])
    by_value = {value.id: value for value in values}
    parents = await parents_of(session)
    keys = await key_action_ids(session)

    def moment(at: datetime, clock: bool) -> str:
        """A moment counted from today: a small model misreads date arithmetic."""
        local = at.astimezone(timezone)
        days = (local.date() - local_day).days
        label = {0: "today", 1: "tomorrow"}.get(days) or (
            f"{local:%d.%m}" if local.year == local_day.year else f"{local:%d.%m.%Y}"
        )
        if clock:
            label += f" {local:%H:%M}"
        passed = local < local_now if clock else days < 0
        if passed:
            return label + (f" overdue {counted(-days, 'day')}" if days < 0 else " overdue")
        return label + (f" in {counted(days, 'day')}" if days > 1 else "")

    async def focus(cards: list[Card]) -> list[Card]:
        return await order_cards(session, cards, now=local_now, focus=True)

    async def card_line(card: Card, executions: int | None = 1) -> str:
        fields = [] if card.priority == Priority.MEDIUM.value else [f"priority={card.priority}"]
        if card.kind == CardKind.GOAL.value:
            fields.append(f"stage={card.effective_stage}")
            fields.append(f"tree_level={await tree_level(session, card.kind, card.id)}")
            if card.deadline_at:
                fields.append(
                    "deadline=" + moment(card.deadline_at, bool(card.schedule_record.rule.get("time")))
                )
        else:
            if card.id in keys:
                fields.append("key")
            if card.blocked:
                fields.append("blocked")
            if executions != 1:
                fields.append(f"executions={executions if executions is not None else '?'}")
            if effort_on:
                fields.append(f"effort={effort_label(card.effort_points)}")
            if card.scheduled_at:
                fields.append(
                    "at=" + moment(card.scheduled_at, not card.schedule_record.rule.get("all_day"))
                )
        if served := serves.get(card.id):
            fields.append("values=" + ", ".join(by_value[value_id].name for value_id in served))
        if above := parents.get(card.id):
            names = [(await session.get(Card, parent_id)).title for parent_id in above]
            fields.append("under=" + ", ".join(
                citation(" ".join(name.split()), "card", parent_id)
                for name, parent_id in zip(names, above, strict=True)
            ))
        return " ".join([f"- {citation(' '.join(card.title.split()), 'card', card.id)}", *fields])

    async def ranked(heading: str, cards: list[Card]) -> list[str]:
        shown = cards[:CONTEXT_LIST_LIMIT]
        return section(
            f"{heading}, best first ({len(shown)} of {len(cards)}):",
            [await card_line(card) for card in shown],
        )

    if sprint is None:
        counts = {value.id: [0, 0, 0] for value in values}
        for card in open_cards:
            for value_id in serves.get(card.id, ()):
                counts[value_id][card.kind == CardKind.ACTION.value] += 1
        for value_id in await session.scalars(
            select(CheckValue.value_id)
            .join(Check, Check.id == CheckValue.check_id)
            .where(Check.archived_at.is_(None), Check.outcome.is_(None))
        ):
            counts[value_id][2] += 1
        value_lines = section(
            "Values, each with the open Goals, Actions and Checks that serve it:",
            [
                described(
                    value.name, "value", value.id, value.description,
                    " ".join(
                        f"{name}={count}"
                        for name, count in zip(("goals", "actions", "checks"), counts[value.id], strict=True)
                        if count
                    ),
                )
                for value in values
            ],
        )
    else:
        value_lines = section(
            "Values:",
            [described(value.name, "value", value.id, value.description) for value in values],
        )
    lines = [
        f"Workspace mode: {workspace.mode if workspace else 'planning'}",
        f"About me: {(profile.about_me if profile else '').strip()}",
        f"Advisor instructions: {(profile.advisor_instructions if profile else '').strip()}",
        f"Effort Points: {'on' if effort_on else 'off'}",
        *value_lines,
        *section(
            "Available Tags:", [described(tag.name, "tag", tag.id, tag.description) for tag in tags]
        ),
    ]
    if sprint is not None:
        # The day is counted here, not by the model: a small model misreads date arithmetic.
        day, days = sprint_day(sprint, local_day)
        lines.append(
            f"Sprint {sprint.number}: {sprint.planned_start_date} – {sprint.planned_end_date}, "
            f"{days} days. Today is day {day}. Days left after today: {max(days - day, 0)}."
        )
        if effort_on and sprint.capacity_effort_points is not None:
            lines.append(f"Sprint capacity: {effort_label(sprint.capacity_effort_points)} EP.")
        lines.append(f"Success criteria: {sprint.success_criteria.strip()}")
    else:
        length = await sprint_length_days(session)
        lines.append(
            "No Sprint is running; the workspace is in Planning. "
            + (
                f"Draft Success criteria for the next one: "
                f"{workspace.sprint_success_criteria.strip()}"
                if workspace and workspace.sprint_success_criteria.strip()
                else "No Success criteria have been written yet."
            )
        )
        lines.append(
            f"A Sprint started today runs {local_day} – {local_day + timedelta(days=length - 1)}, "
            f"{length} days."
        )
        if (capacity := await capacity_effort_points(session)) is not None:
            lines.append(f"Next Sprint's capacity: {effort_label(capacity)} EP.")
    today = await focus(await today_actions(session))
    load = await plan_load(session, today, start_date=local_day, end_date=local_day)
    if today:
        left = ("at least " if load.unknown_schedules else "") + counted(load.actions, "execution")
        if effort_on:
            left += f" and {effort_label(load.effort)} EP"
        left += " left"
        if effort_on and load.unestimated:
            left += f", {counted(load.unestimated, 'execution')} unestimated"
        lines.append(f"Today Actions, best first ({len(today)} of {len(today)}; {left}):")
        lines += [await card_line(card, load.counts.get(card.id, 1)) for card in today]
    else:
        lines.append("Today Actions: none.")
    in_sprint = await focus(await actions_on_stages(session, CardStage.SPRINT))
    goals = [card for card in open_cards if card.kind == CardKind.GOAL.value]
    if sprint is not None:
        above: set[int] = set()
        waiting = [card.id for card in [*today, *in_sprint]]
        while waiting:
            for parent_id in parents.get(waiting.pop(), ()):
                if parent_id not in above:
                    above.add(parent_id)
                    waiting.append(parent_id)
        lines += await ranked("Sprint Actions not in Today", in_sprint)
        lines += await ranked(
            "Sprint Goals", await focus([goal for goal in goals if goal.id in above])
        )
    else:
        lines += await ranked("Goals to consider", await focus(goals))
        lines += await ranked(
            "Backlog Actions to consider",
            await focus(await actions_on_stages(session, CardStage.BACKLOG)),
        )
        lines += await ranked("Still in Sprint, to review", in_sprint)
    return StateBlocks(
        state="\n".join(lines),
        clock=f"Current local time: {local_now:%Y-%m-%d %H:%M} ({timezone})",
    )


# How many open items the mutator is handed by what the conversation is about. The Advisor
# is handed none: given them, it checks them itself instead of handing the change over.
CLOSE_ITEMS_MAX = 10
CLOSE_ITEMS = "Open items close to what the user said, closest first:"
_CLOSE_VIEWS = ("ai_cards", "ai_checks")


async def close_items(context: AgentContext, conversation: str) -> str:
    """Up to CLOSE_ITEMS_MAX open Goals, Actions and Checks close to the end of the
    `conversation` by words or by meaning, the closest first; none close, and nothing. Values
    and Tags are not among them: the workspace state lists them whole."""
    closeness = await closeness_to(context.query_runner.search, conversation, _CLOSE_VIEWS)
    found = {kind: [item_id for each, item_id in closeness if each == kind] for kind in ("card", "check")}
    if not closeness:
        return ""
    async with context.sessions() as session:
        checks = list(
            await session.scalars(
                select(Check).where(
                    Check.id.in_(found["check"]), Check.archived_at.is_(None), Check.outcome.is_(None)
                )
            )
        )
        on = {
            check_id: citation(" ".join(title.split()), "card", card_id)
            for check_id, card_id, title in await session.execute(
                select(CardCheck.check_id, Card.id, Card.title)
                .join(Card, Card.id == CardCheck.card_id)
                .where(CardCheck.check_id.in_([check.id for check in checks]))
            )
        }
        cards = list(
            await session.scalars(
                select(Card).where(
                    Card.id.in_(found["card"]),
                    Card.archived_at.is_(None),
                    Card.effective_stage != CardStage.DONE.value,
                )
            )
        )
    lines = [
        (
            ("check", check.id),
            "; ".join(
                [f"- {citation(' '.join(check.title.split()), 'check', check.id)} Check"]
                + ([f"schedule: {check.schedule}"] if check.schedule else [])
                + ([f"on {on[check.id]}"] if check.id in on else [])
            ),
        )
        for check in checks
    ] + [
        (
            ("card", card.id),
            "; ".join(
                [f"- {citation(' '.join(card.title.split()), 'card', card.id)} {card.kind.title()}"]
                + (
                    [("deadline" if card.kind == CardKind.GOAL.value else "schedule") + f": {card.schedule}"]
                    if card.schedule
                    else []
                )
            ),
        )
        for card in cards
    ]
    shown = closest(lines, closeness, CLOSE_ITEMS_MAX)
    return "\n".join([CLOSE_ITEMS, *shown]) if shown else ""
