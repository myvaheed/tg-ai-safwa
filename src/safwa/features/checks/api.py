"""What another feature reads of Checks: the method for a Check."""

from __future__ import annotations

from ...foundation.method import Example, Method
from ..schedules.api import ACTION_DAILY_EXECUTIONS_MAX

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
        f"Anything more than {ACTION_DAILY_EXECUTIONS_MAX} times a day is a Check.",
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
