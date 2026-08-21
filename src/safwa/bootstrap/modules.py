"""The one place that knows which features exist.

Adding a feature is a package under `features/` plus one line in `MODULES`. Everything
below is derived: the view catalogue and its allowlist, the proposal capabilities, the
subagent roster and the routing rules the Advisor's prompt carries.

`MODULES` is a constant of import time, so the assembled prompt is built once and the
cacheable prefix stays byte-identical across turns.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..ai.context import SYSTEM_PROMPT_TEMPLATE
from ..ai.sql import SqlView
from ..ai.subagents import RoutedSubagent
from ..features.continuity.module import MODULE as CONTINUITY
from ..features.diary.module import MODULE as DIARY
from ..features.planning.module import MODULE as PLANNING
from ..features.profile.module import MODULE as PROFILE
from ..features.proposals.api import (
    MutationToolSpec,
    ProposalHandler,
    ProposalPresenter,
    ProposalRegistry,
)
from ..features.proposals.module import MODULE as PROPOSALS_FEATURE
from ..features.reminders.module import MODULE as REMINDERS
from ..features.saved_requests.module import MODULE as SAVED_REQUESTS
from .module_manifest import AgentContext, BackgroundTask, FeatureModule

# Order matters in two places: the routing rules follow it, and so do the recovery hooks,
# where the Diary's own Reminder has to exist before the Reminder reconcile rolls it forward.
MODULES: tuple[FeatureModule, ...] = (
    PLANNING,
    DIARY,
    PROFILE,
    REMINDERS,
    SAVED_REQUESTS,
    PROPOSALS_FEATURE,
    CONTINUITY,
)


def _views() -> tuple[SqlView, ...]:
    collected: dict[str, SqlView] = {}
    for module in MODULES:
        for view in module.views:
            if view.name in collected:
                raise RuntimeError(f"Two features publish the view {view.name}")
            collected[view.name] = view
    return tuple(collected.values())


AI_VIEWS: tuple[SqlView, ...] = _views()
ALLOWED_VIEWS: frozenset[str] = frozenset(view.name for view in AI_VIEWS)


def _proposals() -> ProposalRegistry:
    handlers: dict[str, ProposalHandler] = {}
    presenters: dict[str, ProposalPresenter] = {}
    tools: dict[str, MutationToolSpec] = {}

    def register_tool(tool: MutationToolSpec) -> None:
        if tool.name in tools:
            raise RuntimeError(f"Two features publish the mutation tool {tool.name}")
        tools[tool.name] = tool

    for module in MODULES:
        for contribution in module.proposals:
            entity = contribution.handler.entity
            if entity in handlers:
                raise RuntimeError(f"Two features own {entity} proposals")
            if contribution.presenter.entity != entity:
                raise RuntimeError(
                    f"The {entity} presenter is registered against "
                    f"{contribution.presenter.entity}"
                )
            handlers[entity] = contribution.handler
            presenters[entity] = contribution.presenter
            register_tool(contribution.tool)
        for tool in module.mutation_tools:
            register_tool(tool)
    return ProposalRegistry(
        handlers=handlers, presenters=presenters, tools=tools, views=ALLOWED_VIEWS
    )


PROPOSALS: ProposalRegistry = _proposals()

AGENTS = tuple(agent for module in MODULES for agent in module.agents)


def _routing_rules() -> str:
    unknown = {
        name
        for agent in AGENTS
        for name in agent.mutation_tools
        if name not in PROPOSALS.tools
    }
    if unknown:
        raise RuntimeError(f"A subagent declares tools no feature publishes: {sorted(unknown)}")
    return "\n".join(f'- `route("{agent.name}")` — {agent.purpose}' for agent in AGENTS)


# The routing rules are prose in the prompt, so a subagent they omit is never routed to.
SYSTEM_PROMPT: str = SYSTEM_PROMPT_TEMPLATE.replace("{routes}", _routing_rules())

RECOVERY_HOOKS: tuple[Callable[..., Awaitable[None]], ...] = tuple(
    module.recover for module in MODULES if module.recover is not None
)

BACKGROUND_TASKS: tuple[BackgroundTask, ...] = tuple(
    task for module in MODULES for task in module.background
)


def routed_subagents(context: AgentContext) -> tuple[RoutedSubagent, ...]:
    """Bind every declared subagent to this application's read tools and clock."""
    return tuple(
        RoutedSubagent(
            name=agent.name,
            purpose=agent.purpose,
            instructions=agent.instructions,
            read_tools=agent.read_tools(context) if agent.read_tools else (),
            mutation_tools=agent.mutation_tools,
            planning_state=agent.planning_state,
            clock=agent.clock(context) if agent.clock else None,
        )
        for agent in AGENTS
    )
