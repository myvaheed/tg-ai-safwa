"""Memory's hook: an analysed Sprint is taken into what is remembered, on a tick.

The tick is the whole of the reliability: an analysis stays owed until a run has written
it, so nothing that ends one attempt is lost, and the next tick is the next try.
"""

from __future__ import annotations

from datetime import timedelta

from tg_agent_shell.hooks.contracts import HookSpec, OnTick, Run, RunContext, Tick

from .use_cases import absorb_due

MEMORY_RETRO_INTERVAL_SECONDS = 60.0


async def every_look(event: Tick) -> tuple[Tick, ...]:
    return (event,)


async def absorb_analysis(event: Tick, context: RunContext) -> None:
    # A tick's work runs on whatever the owner does, so this one takes the lease itself.
    await absorb_due(
        context.resources.memory_reviewer,
        context.sessions,
        run_background=context.resources.run_background,
    )


MEMORY_RETRO_HOOK = HookSpec(
    name="memory.retro",
    owner="memory",
    on=(OnTick(every=timedelta(seconds=MEMORY_RETRO_INTERVAL_SECONDS)),),
    evaluate=every_look,
    effect=Run(absorb_analysis),
    title="Memory from the retro",
    description="Takes the oldest analysed Sprint memory has not taken in into what Safwa remembers.",
)
