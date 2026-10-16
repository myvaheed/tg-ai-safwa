"""What another feature may ask about a Value: an answer, never a row."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.changes import record_change

from ...foundation.method import Example, Method
from .model import CardValue, Value
from .model import CheckValue as CheckValue

CARD_VALUE_LINKED = "card.value_linked"
CHECK_VALUE_LINKED = "check.value_linked"
# One fact for every item one change took the Value off, so its follow-up is one request.
VALUE_UNLINKED = "value.unlinked"


async def unlinkable_value_id(session: AsyncSession, value_ids: Iterable[int]) -> int | None:
    """The first id that cannot be linked, because no live Value has it."""
    wanted = set(value_ids)
    if not wanted:
        return None
    live = set(
        await session.scalars(
            select(Value.id).where(Value.id.in_(wanted))
        )
    )
    return next((value_id for value_id in sorted(wanted) if value_id not in live), None)


async def attach_values(session: AsyncSession, card_id: int, value_ids: Iterable[int]) -> None:
    """Put a Card's Value links in place. The Card owns the operation, this row is ours."""
    for value_id in sorted(set(value_ids)):
        session.add(CardValue(card_id=card_id, value_id=value_id))
        record_change(session, CARD_VALUE_LINKED, card_id, data={"value_id": value_id})


def record_unlinked(session: AsyncSession, value: Value, items: list[dict[str, Any]]) -> None:
    """Note the items `value` was taken off, `{"type": "card" | "check", "id": ...}` each,
    with the Value's words, which a deletion leaves nowhere else to read."""
    if items:
        record_change(session, VALUE_UNLINKED, value.id, data={
            "value": value.name, "value_description": value.description, "items": items,
        })


async def value_names(session: AsyncSession) -> dict[int, str]:
    """Every Value's name by its id, in priority order (highest first)."""
    return {
        value_id: name
        for value_id, name in await session.execute(
            select(Value.id, Value.name).order_by(Value.order)
        )
    }


VALUE = Method(
    name="Value",
    definition="Value: a direction the user lives by, with no deadline. It is never Done.",
    criteria=(
        "a direction, not a result that can be Done;",
        "not one practice such as running; a practice is a repeating Action;",
        "no feeling to have in its name, such as happy or calm;",
        "toward something, not away from something;",
        "an end, not a means: a name such as money or a skill needs a description of what it "
        "gives the user in itself, not only what it buys;",
        "chosen, not a rule with must, always, never or perfect in its name;",
        "in the user's hands: the name may be about others, such as their smile, when the "
        "description says what the user does;",
        "concrete: the description says what the user does for it on an ordinary day.",
    ),
    leaves=(
        "A name such as Health, Security or Freedom is fine. The description shows the doing.",
        "Do not judge whether the Value is right for the user, or how vivid its words are.",
    ),
    writing=(
        "A Value is not a result, a practice, a feeling, a means or a rule.",
        "Value name: short and vivid.",
        "Value description: two or three vivid sentences on why it matters "
        "and what the user does for it on an ordinary day.",
        "Build both from the user's words. Never invent why it matters or what they do.",
    ),
    examples=(
        Example('"Health", "Sleep 7 hours, move every day"', clear=True),
        Example(
            '"📖 Endless learning", "A mind that never stops growing. I read, think deeply '
            'and learn from those who know more."',
            clear=True,
        ),
        Example(
            '"👴👵 Their smile is my victory", "They gave me life and sleepless nights; their '
            'pride is worth more than any title. I call every day and visit every weekend."',
            clear=True,
        ),
        Example(
            '"Money", "With money I feel strong; without it I am afraid to live. '
            'I save a third of every income."',
            clear=True,
        ),
        Example(
            '"👴👵 Their smile is my victory", "Their peace is my peace; their pride is worth '
            'more than any title."',
            clear=False,
            verdict="Says what the parents feel, not what the user does.",
            ask="Ask how the user brings that smile in an ordinary week, such as calls or visits.",
        ),
        Example(
            '"⌛ Greed for time", "Every minute is life that will not come back."',
            clear=False,
            verdict="Says why it matters, not what the user does.",
            ask="Ask how it looks on an ordinary day, such as planning the day the evening before.",
        ),
        Example(
            '"Family", "Very important to me"',
            clear=False,
            verdict="Does not say what exactly matters or what the user does.",
            ask="Ask what in Family touches the user, and how it looks on an ordinary weekday.",
        ),
        Example(
            '"Learn Spanish", "To live in Spain"',
            clear=False,
            verdict="A result that can be Done, so a Goal.",
            ask="Suggest a Goal and ask which Value it serves, such as Growth or Freedom.",
        ),
        Example(
            '"Money", "To buy a house and afford luxury"',
            clear=False,
            verdict="A means: the description says only what it buys.",
            ask="Ask which of these matters in itself, such as Comfort, and suggest it as the Value.",
        ),
        Example(
            '"Be happy", "Smile more"',
            clear=False,
            verdict="A feeling in the name.",
            ask="Ask what the user does on a day they live it.",
        ),
        Example(
            '"Always be productive", "Never waste a day"',
            clear=False,
            verdict="A rule with always.",
            ask="Ask whether it is want or must; suggest Craft.",
        ),
        Example(
            '"Not be like my father", "Never shout at home"',
            clear=False,
            verdict="Away from something.",
            ask="Ask what the user wants instead.",
        ),
        Example(
            '"Be respected", "People listen to me"',
            clear=False,
            verdict="Others decide it, and nothing says what the user does.",
            ask="Ask what is in the user's hands, such as Dignity or Honesty.",
        ),
        Example(
            '"Running", "I love it"',
            clear=False,
            verdict="One practice, a repeating Action.",
            ask="Ask what it serves, such as Health or Freedom.",
        ),
    ),
)

LINK = Method(
    name="Value link",
    definition=(
        "Value link: a Goal, Action or Check serves a Value when achieving the Goal, doing "
        "the Action or the Check holding makes more of the Value in the user's life, "
        "as its description says."
    ),
    criteria=(
        "Never link because the Value is needed to complete the item.",
        "The user's own clear explanation can make a link.",
    ),
    leaves=("An obvious benefit needs no written explanation.",),
    writing=("If a link is unclear, ask how the item serves the Value; explain in `note`.",),
    examples=(
        Example('Goal "Earn $4k" → Freedom', clear=True, verdict="Obviously: more choices."),
        Example(
            'Goal "Earn $4k", Note "Pays for my healthcare" → Health',
            clear=True,
            verdict="The Note says how.",
        ),
        Example(
            'Goal "Earn $4k", Note "I need good health to work" → Health',
            clear=False,
            verdict="Reversed: Health is needed for the Goal.",
        ),
        Example(
            'Goal "Earn $4k" → Perseverance',
            clear=False,
            verdict="Earning money does not make more Perseverance.",
        ),
        Example(
            'Goal "Earn $20k" → Punctuality, "I respect myself when I am on time"',
            clear=False,
            verdict="Reversed: being on time helps earn; earning does not make more of it.",
        ),
        Example(
            'Check "On time?" → Punctuality, "I respect myself when I am on time"',
            clear=True,
        ),
    ),
)
