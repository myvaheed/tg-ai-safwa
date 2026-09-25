"""The onboarding's hooks: a tip after each created or finished item, one notice before
Safwa's first answer, and what still stands when the owner writes after a long break.

The tip is a request to the Advisor to hand the items to the onboarding subagent, worded
here from what each item is now: the subagent reads no data, so the state it explains from
is the state this request cites. The notice follows the tips' switch and has none of its own.
The return has its own switch: the owner who stopped the tips may still want it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed, record_change
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.hooks.contracts import (
    Advise,
    BeforeTurn,
    HookSpec,
    OnBeforeTurn,
    OnCommitted,
    Run,
    RunContext,
    Shown,
)

from ..cards.api import CardStage, planned_actions
from ..cards.model import Card, CardCheck, CardKind
from ..cards.use_cases import CARD_CREATED, CARD_DONE, CARD_TODAY
from ..checks.model import CHECK_OUTCOME_LABELS, Check
from ..checks.use_cases import CHECK_ANSWERED, CHECK_CREATED, check_card_id
from ..diary.model import DiaryEntry
from ..diary.use_cases import DIARY_WRITTEN
from ..planning.api import SPRINT_STARTED, sprint_is_active
from ..planning.model import Sprint
from ..reminders.model import Reminder
from ..reminders.use_cases import REMINDER_CREATED
from ..retro.use_cases import SPRINT_ANALYSED
from ..saved_requests.model import SavedRequest
from ..saved_requests.use_cases import REQUEST_CREATED
from ..tags.model import Tag
from ..tags.use_cases import TAG_CREATED
from ..values.model import CardValue, Value
from ..values.use_cases import VALUE_CREATED
from .model import OnboardingNotice, OwnerPresence

# How many items one tip cites; the rest are counted, so a screen that held many saves back
# still gives one short tip.
ONBOARDING_TIP_ITEMS = 5

# How many days since the owner's last message make their next one a return.
RETURN_AFTER_DAYS = 14
# How many Actions the return lists; the rest are counted, Today's listed first.
RETURN_LIST_ACTIONS = 30

# The owner wrote after a break; the subject is how many days it lasted.
OWNER_RETURNED = "owner.returned"

_INDENT = "    "

# Each fact the subagent has something to say about: the item it is about, and the verb.
# `CARD_TODAY` has no verb: the system puts a repeating Action's copy in Today, so the
# request says where the Card is, never that the user moved it.
TIP_FACTS: dict[str, tuple[str, str | None]] = {
    CARD_CREATED: ("card", "created"),
    CARD_TODAY: ("card", None),
    CARD_DONE: ("card", "finished"),
    CHECK_CREATED: ("check", "created"),
    CHECK_ANSWERED: ("check", "answered"),
    VALUE_CREATED: ("value", "created"),
    TAG_CREATED: ("tag", "created"),
    REQUEST_CREATED: ("request", "created"),
    REMINDER_CREATED: ("reminder", "created"),
    DIARY_WRITTEN: ("diary", "wrote"),
    SPRINT_STARTED: ("sprint", "started"),
    SPRINT_ANALYSED: ("retro", "analysed"),
}

TIP_REQUEST = "Onboarding. The user just:\n{items}\nCall route(\"onboarding\") with these."

ONBOARDING_NOTICE = (
    "Onboarding is on. After you create or finish something, a short tip will explain what "
    "happened and what it makes possible. Ask me anything about Safwa at any time — for "
    'example, "what can Safwa do?". If you do not want onboarding, say so and it stops.'
)

_KIND_NAMES = {
    CardKind.GOAL.value: "a Goal",
    CardKind.SUBGOAL.value: "a Subgoal",
    CardKind.ACTION.value: "an Action",
}


async def what_changed(event: Committed) -> tuple[tuple[str, int], ...]:
    return ((event.kind, event.subject_id),)


def _count(number: int, noun: str) -> str:
    return f"no {noun}" if number == 0 else f"1 {noun}" if number == 1 else f"{number} {noun}s"


async def _card_state(session: AsyncSession, card: Card) -> str:
    checks = await session.scalar(
        select(func.count()).select_from(CardCheck).where(CardCheck.card_id == card.id)
    )
    values = await session.scalar(
        select(func.count()).select_from(CardValue).where(CardValue.card_id == card.id)
    )
    parts = [f"{_KIND_NAMES.get(card.kind, 'a Card')} in {card.effective_stage.title()}"]
    if card.repeatable:
        parts.append("repeats")
    parts += [_count(checks or 0, "Check"), _count(values or 0, "Value")]
    return ", ".join(parts)


async def _check_state(session: AsyncSession, check: Check) -> str:
    card_id = await check_card_id(session, check.id)
    card = await session.get(Card, card_id) if card_id is not None else None
    parts = [f"on Card [{card.title}](card:{card.id})" if card is not None else "on no Card"]
    if check.repeatable:
        parts.append("repeats")
    if check.outcome is not None:
        parts.append(f"answered {CHECK_OUTCOME_LABELS[check.outcome]}")
    return ", ".join(parts)


def _verbs(kinds: Sequence[str]) -> str:
    verbs = [TIP_FACTS[kind][1] for kind in kinds]
    return " and ".join(dict.fromkeys(verb for verb in verbs if verb))


async def _line(
    session: AsyncSession, item: str, item_id: int, kinds: Sequence[str], answered: Sequence[Check]
) -> str | None:
    """One cited item and its state as it is now, or None for an item that is gone."""
    verbs = _verbs(kinds)
    if item == "card":
        card = await session.get(Card, item_id)
        if card is None:
            return None
        cited = f"[{card.title}](card:{card.id})"
        line = (
            f"{verbs} a Card {cited}" if verbs else f"a Card {cited} is now in Today"
        ) + f" — {await _card_state(session, card)}"
        for check in answered:
            line += (
                f"; answered its Check [{check.title}](check:{check.id}) "
                f"{CHECK_OUTCOME_LABELS.get(check.outcome or 'pending', 'Pending')}"
            )
        return line
    if item == "check":
        check = await session.get(Check, item_id)
        if check is None:
            return None
        return (
            f"{verbs} a Check [{check.title}](check:{check.id}) — "
            f"{await _check_state(session, check)}"
        )
    if item == "value":
        value = await session.get(Value, item_id)
        return None if value is None else f"{verbs} a Value [{value.name}](value:{value.id})"
    if item == "tag":
        tag = await session.get(Tag, item_id)
        return None if tag is None else f"{verbs} a Tag [{tag.name}](tag:{tag.id})"
    if item == "request":
        request = await session.get(SavedRequest, item_id)
        return (
            None
            if request is None
            else f"{verbs} a Request [{request.name}](request:{request.id})"
        )
    if item == "reminder":
        reminder = await session.get(Reminder, item_id)
        return None if reminder is None else f'{verbs} a Reminder "{reminder.instruction}"'
    if item == "diary":
        entry = await session.get(DiaryEntry, item_id)
        return (
            None
            if entry is None
            else f"{verbs} the Diary for a day [{entry.entry_date:%d.%m.%Y}](diary:{entry.id})"
        )
    sprint = await session.get(Sprint, item_id)
    if sprint is None:
        return None
    if item == "retro":
        return (
            f"{verbs} Sprint {sprint.number} — its analysis is on "
            f"[Sprint {sprint.number} retro](retro:{sprint.id})"
        )
    return f"{verbs} Sprint {sprint.number}"


async def onboarding_request(session: AsyncSession, items: Sequence[Any]) -> str | None:
    """The request that hands what the user just did to the onboarding subagent, or nothing.

    One item under several facts is one line, and a Check answered on the Card it closed is
    said on that Card's line. Only items still there are cited, at most
    `ONBOARDING_TIP_ITEMS` of them, and the rest are counted.
    """
    facts: dict[tuple[str, int], list[str]] = {}
    for kind, subject_id in items:
        if kind in TIP_FACTS:
            facts.setdefault((TIP_FACTS[kind][0], int(subject_id)), []).append(kind)
    finished = {
        item_id for (item, item_id), kinds in facts.items() if item == "card" and CARD_DONE in kinds
    }
    answered: dict[int, list[Check]] = {}
    for (item, item_id), kinds in list(facts.items()):
        if item != "check" or kinds != [CHECK_ANSWERED]:
            continue
        card_id = await check_card_id(session, item_id)
        check = await session.get(Check, item_id)
        if card_id in finished and check is not None:
            answered.setdefault(card_id, []).append(check)
            del facts[(item, item_id)]
    lines = [
        line
        for (item, item_id), kinds in facts.items()
        if (
            line := await _line(
                session, item, item_id, kinds, answered.get(item_id, []) if item == "card" else []
            )
        )
    ]
    if not lines:
        return None
    cited = [f"- {line}" for line in lines[:ONBOARDING_TIP_ITEMS]]
    if len(lines) > ONBOARDING_TIP_ITEMS:
        cited.append(f"- and {len(lines) - ONBOARDING_TIP_ITEMS} more")
    return TIP_REQUEST.format(items=";\n".join(cited) + ".")


ONBOARDING_HOOK = HookSpec(
    name="onboarding",
    owner="onboarding",
    on=tuple(OnCommitted(kind=kind) for kind in TIP_FACTS),
    evaluate=what_changed,
    effect=Advise(prepare=onboarding_request),
    title="Onboarding",
    description="After you create or finish something, explains what happened and what it makes possible.",
)


async def always(event: BeforeTurn) -> tuple[BeforeTurn, ...]:
    return (event,)


async def say_once(event: BeforeTurn, context: RunContext) -> None:
    """Publish the notice unless it was sent, then write down that it was.

    Published first and written after: a process that stops between the two sends it once
    more, and nothing guards against that.
    """
    async with context.sessions() as session:
        if await session.get(OnboardingNotice, 1) is not None:
            return
    if not context.still_current():
        return
    await context.publish(ONBOARDING_NOTICE, MessageKind.DIALOGUE_ASSISTANT.value)
    async with context.sessions() as session:
        session.add(OnboardingNotice(id=1))
        await session.commit()


NOTICE_HOOK = HookSpec(
    name="onboarding.notice",
    owner="onboarding",
    switch=ONBOARDING_HOOK.name,
    on=(OnBeforeTurn(),),
    evaluate=always,
    effect=Run(say_once),
    title="Onboarding notice",
    description="Before Safwa's first answer, says that onboarding is on and how to stop it.",
)


async def owner_turns(event: BeforeTurn) -> tuple[BeforeTurn, ...]:
    return (event,) if event.source == "owner" else ()


async def note_presence(event: BeforeTurn, context: RunContext) -> None:
    """Write down that the owner wrote, and record a return when the last time was long ago."""
    now = utcnow()
    async with context.sessions() as session:
        presence = await session.get(OwnerPresence, 1)
        if presence is None:
            session.add(OwnerPresence(id=1, last_message_at=now))
        else:
            days = (now - presence.last_message_at).days
            presence.last_message_at = now
            if days >= RETURN_AFTER_DAYS:
                record_change(session, OWNER_RETURNED, days)
        await session.commit()


PRESENCE_HOOK = HookSpec(
    name="onboarding.presence",
    owner="onboarding",
    on=(OnBeforeTurn(),),
    evaluate=owner_turns,
    effect=Run(note_presence),
    title="Last message",
    description="Before each answer to the owner's message, writes down when it came.",
)


async def days_away(event: Committed) -> tuple[int, ...]:
    return (event.subject_id,)


def _cited(card: Card) -> str:
    return f"[{card.title}](card:{card.id})"


async def _tree(session: AsyncSession, actions: Sequence[Card]) -> list[str]:
    """Each Action under its Goal, and under its Subgoal when it has one, Goals in the order
    they were made, and the Actions with no Goal last, in a group of their own."""
    parents: dict[int, Card] = {}
    groups: dict[int | None, dict[int | None, list[Card]]] = {}
    for action in actions:
        goal = await session.get(Card, action.parent_id) if action.parent_id else None
        subgoal = None
        if goal is not None and goal.kind == CardKind.SUBGOAL.value:
            subgoal = goal
            goal = await session.get(Card, subgoal.parent_id) if subgoal.parent_id else None
        parents.update({card.id: card for card in (goal, subgoal) if card is not None})
        under_goal = groups.setdefault(goal.id if goal else None, {})
        under_goal.setdefault(subgoal.id if subgoal else None, []).append(action)
    lines: list[str] = []
    for goal_id in sorted(groups, key=lambda key: (key is None, key or 0)):
        lines.append(_cited(parents[goal_id]) if goal_id is not None else "No Goal")
        under_goal = groups[goal_id]
        for subgoal_id in sorted(under_goal, key=lambda key: (key is not None, key or 0)):
            indent = _INDENT
            if subgoal_id is not None:
                lines.append(_INDENT + _cited(parents[subgoal_id]))
                indent = _INDENT * 2
            lines += [
                f"{indent}{_cited(action)} — {action.effective_stage.title()}"
                for action in under_goal[subgoal_id]
            ]
    return lines


async def return_request(session: AsyncSession, items: Sequence[Any]) -> Shown:
    """What stands in Today and the Sprint, as a block, and what the Advisor asks about it.

    At most `RETURN_LIST_ACTIONS` Actions are listed, Today's first, and the rest counted.
    """
    days = max(int(item) for item in items)
    opening = f"It has been {days} days since your last message."
    back = f"The user is back after {days} days away."
    actions = sorted(
        await planned_actions(session),
        key=lambda card: (card.effective_stage != CardStage.TODAY.value, card.id),
    )
    if not actions:
        return Shown(
            block=f"{opening} Today and the Sprint hold nothing.",
            request=f"{back} Today and the Sprint are empty. Offer to plan what comes next.",
        )
    lines = [f"{opening} In Today and the Sprint:", ""]
    lines += await _tree(session, actions[:RETURN_LIST_ACTIONS])
    if len(actions) > RETURN_LIST_ACTIONS:
        lines.append(f"And {len(actions) - RETURN_LIST_ACTIONS} more Actions.")
    request = f"{back} Ask which of the listed Actions still matter to them."
    if not await sprint_is_active(session):
        request += " No Sprint is running: offer to start a new one."
    return Shown(block="\n".join(lines), request=request)


RETURN_HOOK = HookSpec(
    name="onboarding.return",
    owner="onboarding",
    on=(OnCommitted(kind=OWNER_RETURNED),),
    evaluate=days_away,
    effect=Advise(prepare=return_request),
    title="Return after a break",
    description=(
        f"When you write after {RETURN_AFTER_DAYS} days or more away, shows what stands in "
        "Today and the Sprint and asks what still matters."
    ),
)
