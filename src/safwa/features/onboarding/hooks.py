"""The onboarding's two hooks: a tip after each created or finished item, and one notice
before Safwa's first answer.

The tip is a request to the Advisor to hand the items to the onboarding subagent, worded
here from what each item is now: the subagent reads no data, so the state it explains from
is the state this request cites. The notice follows the tips' switch and has none of its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import Committed
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.hooks.contracts import (
    Advise,
    BeforeTurn,
    HookSpec,
    OnBeforeTurn,
    OnCommitted,
    Run,
    RunContext,
)

from ..cards.model import Card, CardCheck, CardKind
from ..cards.use_cases import CARD_CREATED, CARD_DONE, CARD_TODAY
from ..checks.model import CHECK_OUTCOME_LABELS, Check
from ..checks.use_cases import CHECK_ANSWERED, CHECK_CREATED, check_card_id
from ..diary.model import DiaryEntry
from ..diary.use_cases import DIARY_WRITTEN
from ..planning.api import SPRINT_STARTED
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
from .model import OnboardingNotice

# How many items one tip cites; the rest are counted, so a screen that held many saves back
# still gives one short tip.
ONBOARDING_TIP_ITEMS = 5

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
