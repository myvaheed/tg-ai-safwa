"""Every nine days, in the Morning, look at the whole tree for what Compass would raise.

The hook's work counts the days from the workspace's first one. On every ninth it writes the
tree out with what SQL found in it, and one small model answers clear or names the findings;
a clear answer still keeps what SQL found. Nothing found asks nothing; what is found is one
request, which hands it to Compass.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import LlmProvider
from tg_agent_shell.ai.contracts import ToolInput
from tg_agent_shell.ai.mini import TerminalTool, run_mini_session
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.hooks.contracts import HookSpec, OnTick, RunAndAdvise, RunContext, Tick

from ...foundation.method import bullets, lines
from ...foundation.workspace import require_workspace
from ..profile.api import morning_time
from .graph import (
    COMPASS_FINDINGS_MAX,
    NOT_PROBLEMS,
    PROBLEMS,
    plain_findings,
    read_tree,
    tree_text,
)

# The days between two looks at the whole tree, counted from the workspace's first day.
COMPASS_REVIEW_DAYS = 9

GRAPH_REVIEW_PROMPT = (
    "You look at the user's whole tree of Values, Goals, Actions and Checks.\n"
    "Find what could be set better.\n\n"
    "What could be set better:\n"
    f"{bullets(PROBLEMS)}"
    f"{lines(NOT_PROBLEMS)}"
    "The lines under Found without a model are facts.\n\n"
    f"Call findings with at most {COMPASS_FINDINGS_MAX}, the most important first, "
    "each one line citing its items as the tree cites them.\n"
    "Call clear when nothing stands out."
)

GRAPH_REQUEST = (
    "Compass. Looking at the whole tree of Values, Goals, Actions and Checks found:\n"
    "{findings}\n"
    'Call route("compass") with these.'
)


class GraphClearInput(ToolInput):
    """Nothing stands out in the tree."""


class GraphFindingsInput(ToolInput):
    findings: list[str] = Field(
        min_length=1,
        max_length=COMPASS_FINDINGS_MAX,
        description="Each finding in one line, citing its items.",
    )


GRAPH_CLEAR_TERMINAL = TerminalTool("clear", "Nothing stands out.", GraphClearInput)
GRAPH_FINDINGS_TERMINAL = TerminalTool(
    "findings", "What could be set better.", GraphFindingsInput
)


class GraphReviewer:
    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def findings(self, tree: str) -> list[str]:
        """What stands out in the written-out tree, or nothing."""
        answer = await run_mini_session(
            self.provider,
            system_prompt=GRAPH_REVIEW_PROMPT,
            context=tree,
            terminals=(GRAPH_CLEAR_TERMINAL, GRAPH_FINDINGS_TERMINAL),
            max_tool_calls=1,
            max_repairs=1,
        )
        if answer.name == GRAPH_CLEAR_TERMINAL.name:
            return []
        return answer.payload.findings


class GraphReviewResources(Protocol):
    graph_reviewer: GraphReviewer


async def morning(event: Tick) -> tuple[str, ...]:
    return (event.at,)


async def review_day(session: AsyncSession, *, now: datetime | None = None) -> bool:
    """Whether today is a whole number of `COMPASS_REVIEW_DAYS` after the workspace's first
    day, counted on its local days."""
    workspace = await require_workspace(session)
    zone = ZoneInfo(workspace.timezone)
    days = ((now or utcnow()).astimezone(zone).date() - workspace.created_at.astimezone(zone).date()).days
    return days > 0 and days % COMPASS_REVIEW_DAYS == 0


async def review_graph(
    at: str, context: RunContext[GraphReviewResources]
) -> tuple[str, ...]:
    """The findings to hand to Compass, on a review day with a tree to look at."""
    async with context.sessions() as session:
        if not await review_day(session):
            return ()
        tree = await read_tree(session)
    if not tree.values and not tree.cards:
        return ()
    found = await context.resources.graph_reviewer.findings(tree_text(tree))
    return tuple(found or plain_findings(tree)[:COMPASS_FINDINGS_MAX])


async def graph_request(session: AsyncSession, items: Sequence[str]) -> str | None:
    if not items:
        return None
    return GRAPH_REQUEST.format(findings="\n".join(f"- {item}" for item in items))


GRAPH_REVIEW_HOOK = HookSpec(
    name="compass.graph_review",
    owner="compass",
    on=(OnTick(at=morning_time),),
    evaluate=morning,
    effect=RunAndAdvise(run=review_graph, prepare=graph_request),
    title="Compass review",
    description=(
        f"Every {COMPASS_REVIEW_DAYS} days in the Morning, looks at the whole tree of Values, "
        f"Goals, Actions and Checks. Raises at most {COMPASS_FINDINGS_MAX} things that could be "
        "set better, and says nothing when none stands out."
    ),
)
