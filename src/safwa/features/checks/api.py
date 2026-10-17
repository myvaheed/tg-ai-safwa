"""What another feature reads of Checks: the method for a Check, and a run of answers."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import takewhile
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...foundation.method import Example, Method
from .model import Check


async def newest_answers(session: AsyncSession, check_ids: Sequence[int]) -> list[Any]:
    """These Checks' answers, newest first, each with its id, outcome and resolved_at.

    Pending is skipped, so the open instance on top neither counts nor breaks a run.
    """
    return list(
        (
            await session.execute(
                select(Check.id, Check.outcome, Check.resolved_at)
                .where(Check.id.in_(check_ids), Check.outcome.is_not(None))
                .order_by(Check.resolved_at.desc(), Check.id.desc())
            )
        ).all()
    )


def answer_run(answers: Sequence[Any], outcome: str) -> list[Any]:
    """The newest answers back to the first other one."""
    return list(takewhile(lambda row: row.outcome == outcome, answers))

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
