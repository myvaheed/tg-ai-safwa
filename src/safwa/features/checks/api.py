"""What another feature reads of Checks: the method for a Check, a Check's series, whether
it repeats and the run of answers it ends on."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.method import Example, Method
from ..cards.model import Card, CardCheck
from ..schedules.rules import repeats
from .model import Check


async def series_of(session: AsyncSession, check_id: int) -> int | None:
    """The series a Check belongs to, or None when there is no such Check."""
    check = await session.get(Check, check_id)
    return None if check is None else check.series_id or check.id


async def check_repeats(session: AsyncSession, check_id: int) -> bool:
    """Whether a Check's series goes on: by its own Schedule, or by the Action it hangs on.

    Neither has a last occurrence when it repeats, so a Check is answered once or without end.
    """
    check = await session.get(Check, check_id)
    card = await session.scalar(
        select(Card)
        .join(CardCheck, CardCheck.card_id == Card.id)
        .where(CardCheck.check_id == check_id)
    )
    definitions = (check.schedule_record, card.schedule_record if card is not None else None)
    return any(
        definition is not None and definition.rule and repeats(definition.rule)
        for definition in definitions
    )


async def answer_run(session: AsyncSession, series_id: int, outcome: str) -> list[Check]:
    """The series' newest answers back to the first other answer, newest first.

    Pending is skipped, so the open instance on top neither counts nor breaks the run.
    """
    answered = await session.scalars(
        select(Check)
        .where(
            or_(Check.series_id == series_id, Check.id == series_id),
            Check.outcome.is_not(None),
        )
        .order_by(Check.id.desc())
    )
    run: list[Check] = []
    for instance in answered:
        if instance.outcome != outcome:
            break
        run.append(instance)
    return run

CHECK = Method(
    name="Check",
    definition="Check: a yes/no observation with no duration, on one Card or on none.",
    criteria=(
        "a state or a result to observe, not work to do: when the honest answer is "
        '"I did it", it is an Action, except not doing something, such as No sweets?;',
        "has no duration; anything that takes time is an Action;",
        "answered yes or no;",
        "observable: some fact shows the answer;",
        "on a Card, about that Card's result.",
    ),
    leaves=("Do not judge whether the Check is worth keeping.",),
    writing=(
        "Check title: a short yes/no question, or one part of its Card's result such as Milk.",
    ),
    examples=(
        Example('"Slept 7 hours?", every evening', clear=True),
        Example('"On time?", every weekday', clear=True),
        Example('"No sweets today?", every evening', clear=True),
        Example('"Milk", on "Go to the market"', clear=True),
        Example(
            '"Did my morning exercises?", every morning',
            clear=False,
            verdict="Asks whether work was done.",
            ask="Suggest a repeating Action Morning exercises instead.",
        ),
        Example(
            '"Run 5 km?"',
            clear=False,
            verdict="Work that takes time.",
            ask="Suggest an Action instead.",
        ),
        Example(
            '"How long did I sleep?"',
            clear=False,
            verdict="Not answered yes or no.",
            ask="Suggest Slept 7 hours?.",
        ),
        Example(
            '"Was I a good father?"',
            clear=False,
            verdict="No fact shows it.",
            ask="Ask what fact would show it, such as Played with the kids for an hour?.",
        ),
        Example(
            '"Posture straight?", on "Go to the market"',
            clear=False,
            verdict="Not about its Card's result.",
            ask="Suggest it as a Check of its own with a Schedule.",
        ),
    ),
)
