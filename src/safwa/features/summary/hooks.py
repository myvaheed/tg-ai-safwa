"""Automatic Summary uses the same writer as the explicit command."""

from __future__ import annotations

from typing import Protocol

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.hooks.contracts import (
    AfterTurn,
    HookSpec,
    HookSwitch,
    OnAfterTurn,
    Run,
    RunContext,
)

from .summary import DialogueSummary


class SummaryResources(Protocol):
    summary: DialogueSummary


async def summary_candidate(event: AfterTurn) -> tuple[AfterTurn, ...]:
    # The writer already reads the window and checks its budget. Do not read it twice.
    return (event,)


async def make_summary(event: AfterTurn, context: RunContext[SummaryResources]) -> None:
    await context.resources.summary.close_window(
        event.chat_id,
        lambda text: context.publish(text, MessageKind.SUMMARY.value),
        still_current=context.still_current,
    )


SUMMARY_HOOK = HookSpec(
    name="summary.window",
    owner="summary",
    on=(OnAfterTurn(),),
    evaluate=summary_candidate,
    effect=Run(make_summary),
    switch=HookSwitch(
        title="Automatic Summary",
        description="After a turn, folds a long dialogue window into a Summary.",
    ),
)
