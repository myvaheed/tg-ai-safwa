"""Advise on a new Action only when it does not fit in one day or its labels clearly miss.

The hook's work reads each new Action, the last exchanges of the conversation and, with
Effort Points on, the closest Actions that carry an estimate, finished ones first; one small
model call answers clear, or unclear with its advice. The unclear one is kept with that
advice as its pending request; every pending Action is worded in one request, which waits
behind the Cue gate like any hook's, and the hook's switch drops what is still pending. Only
creation starts the check, so the change made from the owner's answer asks nothing again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import LlmProvider
from telegram_llm import DialogueMessage
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.conversation import conversation_block, last_exchanges
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session
from tg_agent_shell.ai.sql import Ranking
from tg_agent_shell.hooks.contracts import HookSpec, OnCommitted, RunAndAdvise, RunContext

from ..profile.api import effort_tracking_on
from .goal_review import created_card, open_card
from .model import (
    CATEGORY_MEANINGS,
    EFFORT_RUNGS,
    ENERGY_MEANINGS,
    Card,
    CardCategory,
    CardEnergyType,
    CardKind,
    CardStage,
    effort_label,
)
from .use_cases import CARD_CREATED
from .views import AI_CARDS, CARD_ITEM_TYPE

# How many of the owner's last messages, each with what answered it, the review reads: what
# the owner named there is theirs, and the review leaves it alone.
ACTION_REVIEW_EXCHANGES = 5
# How many Actions with an estimate the review compares a new one's Effort Points with.
SIMILAR_ACTIONS = 10

_CATEGORIES = "; ".join(f"{kind.value}: {words}" for kind, words in CATEGORY_MEANINGS.items())
_ENERGY = "; ".join(f"{kind.value}: {words}" for kind, words in ENERGY_MEANINGS.items())
_RUNGS = "".join(f"- {effort_label(points)}: {words}\n" for points, words in EFFORT_RUNGS.items())

# Whether the Action is worth doing is the owner's to know: its title cannot show it.
ACTION_REVIEW_PROMPT = (
    "You review one new Action: its title, Note, Schedule and labels, "
    "with the recent conversation.\n"
    "An Action is work that fits in one day. A repeating Action fits in one day each time.\n\n"
    "A good Action:\n"
    "- fits in one day; a result that takes longer is a Goal;\n"
    f"- has `categories` that fit, what it gives: {_CATEGORIES};\n"
    f"- has `energy_types` that fit, what it costs: {_ENERGY};\n"
    "- has `effort_points` that fit, when the field is given.\n"
    "Effort Points say how the user carries on after the Action:\n"
    f"{_RUNGS}"
    "A label needs a change only when it clearly does not fit, "
    "or the main one is clearly missing.\n"
    "Compare `effort_points` with `similar`, finished ones first.\n"
    "Advise on `effort_points` only when it clearly differs from them; "
    "name them and their effort points.\n"
    "`effort_points` null means not set: suggest one, from `similar` when there are any.\n"
    "Never advise changing what the user named in the conversation.\n"
    "Do not judge whether the Action is worth doing.\n\n"
    "Examples:\n"
    '- "Run 5 km", rest, physical: clear()\n'
    '- "Pay the rent", chores, cognitive: clear()\n'
    '- "Run 5 km", rest, cognitive: unclear("Running costs the body, not focus. '
    'Suggest physical instead of cognitive.")\n'
    '- "Call mom", no labels: unclear("The main labels are missing. '
    'Suggest people and emotional.")\n'
    '- "Write the thesis": unclear("Takes more than one day, so a Goal. '
    'Suggest a Goal with Actions under it, such as Write chapter 1.")\n'
    '- "Run 20 km", 1 EP, similar finished "Run 15 km" with 5 EP: unclear("1 EP looks low: '
    'the finished Run 15 km took 5. Suggest 5 or 8.")\n'
    '- "Run 20 km", 1 EP, the user asked for 1 EP: clear()\n'
    "Call clear when nothing clearly needs a change.\n"
    "Otherwise call unclear with what to change, why, and one advice."
)

ACTION_REVIEW_REQUEST = (
    "New Actions to improve, each with advice:\n"
    "{cards}\n"
    "Read the current Actions first.\n"
    "If an Action is gone or already follows the advice, skip it.\n"
    "Say that you looked at the new Actions again and they could be improved.\n"
    "For each Action, say briefly what to change and why, following its advice.\n"
    "Offer each change as a possibility, not as the user's choice.\n"
    "Ask whether to make the changes.\n"
    "When they answer, route to workspace_mutator to update the Action, "
    "or to propose a Goal instead.\n"
    "Change nothing until they answer."
)


class ActionClearInput(ToolInput):
    """Nothing to change: the Action fits."""


class ActionUnclearInput(ToolInput):
    advice: str = Field(description="What to change, why, and one advice.")


ACTION_CLEAR_TERMINAL = TerminalTool("clear", "The Action fits.", ActionClearInput)
ACTION_UNCLEAR_TERMINAL = TerminalTool(
    "unclear", "The Action needs a change.", ActionUnclearInput
)


class Dialogue(Protocol):
    async def dialogue(self, chat_id: int) -> Sequence[DialogueMessage]: ...


class ActionReviewer:
    def __init__(
        self, provider: LlmProvider, history: Dialogue, chat_id: int, ranking: Ranking
    ) -> None:
        self.provider = provider
        self.history = history
        self.chat_id = chat_id
        self.ranking = ranking

    async def related(self, title: str) -> Mapping[tuple[str, int], float]:
        """The Cards close to the title by words or meaning, with their relevance."""
        return await self.ranking.relevance(title, (AI_CARDS.name,))

    async def advice(
        self, action: Mapping[str, Any], similar: Sequence[Mapping[str, Any]]
    ) -> str | None:
        """What to change in the Action, or None when it fits."""
        dialogue = [message.as_message() for message in await self.history.dialogue(self.chat_id)]
        context = {**action, "conversation": conversation_block(
            last_exchanges(dialogue, ACTION_REVIEW_EXCHANGES)
        )}
        if "effort_points" in action:
            context["similar"] = list(similar)
        answer = await run_mini_session(
            self.provider,
            system_prompt=ACTION_REVIEW_PROMPT,
            context=json.dumps(context, ensure_ascii=False),
            terminals=(ACTION_CLEAR_TERMINAL, ACTION_UNCLEAR_TERMINAL),
            max_tool_calls=1,
            max_repairs=1,
            reasoning_effort="none",
        )
        if answer.name == ACTION_CLEAR_TERMINAL.name:
            return None
        return answer.payload.advice


class ActionReviewResources(Protocol):
    action_reviewer: ActionReviewer


async def _read_action(session: AsyncSession, card_id: int) -> dict[str, Any] | None:
    """What the review reads of an open Action, or None for anything else. Effort Points
    are read only while the owner uses them."""
    card = await open_card(session, card_id, CardKind.ACTION)
    if card is None:
        return None
    action: dict[str, Any] = {
        "title": card.title,
        "note": card.note,
        "schedule": card.schedule or "",
        "categories": list(await session.scalars(
            select(CardCategory.category)
            .where(CardCategory.card_id == card_id)
            .order_by(CardCategory.category)
        )),
        "energy_types": list(await session.scalars(
            select(CardEnergyType.energy_type)
            .where(CardEnergyType.card_id == card_id)
            .order_by(CardEnergyType.energy_type)
        )),
    }
    if await effort_tracking_on(session):
        action["effort_points"] = card.effort_points
    return action


async def _similar(
    session: AsyncSession, card_id: int, related: Mapping[tuple[str, int], float]
) -> list[dict[str, Any]]:
    """The closest Actions with an estimate, finished ones first, one of each repeat series."""
    scores = {
        item_id: score
        for (item_type, item_id), score in related.items()
        if item_type == CARD_ITEM_TYPE and item_id != card_id
    }
    if not scores:
        return []
    cards = await session.scalars(
        select(Card).where(
            Card.id.in_(list(scores)),
            Card.kind == CardKind.ACTION.value,
            Card.effort_points.is_not(None),
        )
    )
    ordered = sorted(
        cards,
        key=lambda card: (card.effective_stage != CardStage.DONE.value, -scores[card.id], card.id),
    )
    series: set[int] = set()
    similar: list[dict[str, Any]] = []
    for card in ordered:
        if (card.repeat_series_id or card.id) in series:
            continue
        series.add(card.repeat_series_id or card.id)
        similar.append({
            "title": card.title,
            "effort_points": card.effort_points,
            "finished": card.effective_stage == CardStage.DONE.value,
        })
        if len(similar) == SIMILAR_ACTIONS:
            break
    return similar


async def review_action(
    card_id: int, context: RunContext[ActionReviewResources]
) -> tuple[dict[str, Any], ...]:
    """The new Action to advise on, with its advice, when something clearly needs a change
    and it is still what the model read."""
    async with context.sessions() as session:
        asked = await _read_action(session, card_id)
    if asked is None:
        return ()
    reviewer = context.resources.action_reviewer
    similar: list[dict[str, Any]] = []
    if "effort_points" in asked:
        related = await reviewer.related(asked["title"])
        async with context.sessions() as session:
            similar = await _similar(session, card_id, related)
    advice = await reviewer.advice(asked, similar)
    if advice is None:
        return ()
    async with context.sessions() as session:
        if await _read_action(session, card_id) != asked:
            return ()
    return ({"id": card_id, "advice": advice},)


async def action_review_request(
    session: AsyncSession, items: Sequence[dict[str, Any]]
) -> str | None:
    """One request for every Action still open, or nothing when none is."""
    lines = []
    for item in items:
        action = await _read_action(session, item["id"])
        if action is not None:
            lines.append(f"- #{item['id']} «{action['title']}»: {item['advice']}")
    return ACTION_REVIEW_REQUEST.format(cards="\n".join(lines)) if lines else None


ACTION_REVIEW_HOOK = HookSpec(
    name="cards.action_review",
    owner="cards",
    on=(OnCommitted(kind=CARD_CREATED),),
    evaluate=created_card,
    effect=RunAndAdvise(run=review_action, prepare=action_review_request),
    title="Action review",
    description=(
        "After an Action is created, checks that it fits in one day and that its Categories, "
        "Energy types and Effort Points fit it. Suggests changes, with reasons, only when "
        "something clearly does not."
    ),
)
