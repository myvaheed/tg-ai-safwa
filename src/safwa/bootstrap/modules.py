"""The one place that knows which features exist.

Adding a feature is a package under `features/` plus one line in `MODULES`. Everything
below is derived: the view catalogue and its allowlist, the proposal capabilities, the
subagent roster and the routing rules the Advisor's prompt carries.

`MODULES` is a constant of import time, so the assembled prompt is built once and the
cacheable prefix stays byte-identical across turns.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.autoapproval import AutoApprovalRule
from tg_agent_shell.ai.sql import SqlView, view_catalogue
from tg_agent_shell.ai.subagents import RoutedSubagent
from tg_agent_shell.cues.module import CUE_QUEUE
from tg_agent_shell.foundation.screens import (
    ScreenCatalogue,
    ScreenCommand,
    ScreenSpec,
    StartLink,
    TextInputFlow,
)
from tg_agent_shell.proposals.api import (
    MutationToolSpec,
    ProposalHandler,
    ProposalPresenter,
    ProposalRegistry,
    World,
)
from tg_agent_shell.proposals.module import MODULE as PROPOSALS_FEATURE
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec, BackgroundTask, FeatureModule

from ..features.advisor.agent import ADVISOR_VIEWS, SYSTEM_PROMPT_TEMPLATE
from ..features.cards.module import MODULE as CARDS
from ..features.checks.module import MODULE as CHECKS
from ..features.continuity.module import MODULE as CONTINUITY
from ..features.diagnostics.module import MODULE as DIAGNOSTICS
from ..features.diary.module import MODULE as DIARY
from ..features.heavy_analyzer import agent as heavy_analyzer
from ..features.home.module import MODULE as HOME
from ..features.planning.module import MODULE as PLANNING
from ..features.profile.module import MODULE as PROFILE
from ..features.reminders.module import MODULE as REMINDERS
from ..features.retro.module import MODULE as RETRO
from ..features.saved_requests.module import MODULE as SAVED_REQUESTS
from ..features.tags.module import MODULE as TAGS
from ..features.values.module import MODULE as VALUES
from ..features.workspace_mutator.module import MODULE as WORKSPACE_MUTATOR
from ..foundation.workspace import require_workspace

# Order is what the routing rules and the recovery hooks follow, so it is fixed rather than
# incidental: Profile settles the Diary's own Reminder before the Reminder rebuild walks the
# whole table, and the Advisor's prompt lists the subagents in this order every run.
MODULES: tuple[FeatureModule, ...] = (
    HOME,
    WORKSPACE_MUTATOR,
    CARDS,
    CHECKS,
    VALUES,
    TAGS,
    PLANNING,
    RETRO,
    DIARY,
    PROFILE,
    REMINDERS,
    SAVED_REQUESTS,
    PROPOSALS_FEATURE,
    CONTINUITY,
    DIAGNOSTICS,
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


def _screens() -> ScreenCatalogue:
    collected: list[ScreenSpec] = []
    seen: set[str] = set()
    for module in MODULES:
        for spec in module.screens:
            if spec.item_type in seen:
                raise RuntimeError(f"Two features publish the {spec.item_type} screen")
            seen.add(spec.item_type)
            collected.append(spec)
    return ScreenCatalogue.of(tuple(collected))


# What can be opened and what can be cited are the same list, so the deep-link payload
# and both citation patterns are derived from it rather than written out again.
SCREENS: ScreenCatalogue = _screens()


# The shell has commands of its own, so the composition root is what puts the two lists
# together; the order here is the order Telegram publishes them in.
FEATURE_COMMANDS: tuple[ScreenCommand, ...] = tuple(
    command for module in MODULES for command in module.commands
)


def _callback_actions() -> dict[str, Callable[..., Awaitable[None]]]:
    actions: dict[str, Callable[..., Awaitable[None]]] = {}
    for module in MODULES:
        for name, handler in module.callback_actions.items():
            if name in actions:
                raise RuntimeError(f"Two features answer the callback {name}")
            actions[name] = handler
    return actions


FEATURE_CALLBACK_ACTIONS: dict[str, Callable[..., Awaitable[None]]] = _callback_actions()


def _text_inputs() -> dict[str, TextInputFlow]:
    flows: dict[str, TextInputFlow] = {}
    for module in MODULES:
        for flow in module.text_inputs:
            if flow.name in flows:
                raise RuntimeError(f"Two features answer the {flow.name} text input")
            flows[flow.name] = flow
    return flows


FEATURE_TEXT_INPUTS: dict[str, TextInputFlow] = _text_inputs()

# Tried in `MODULES` order; a payload none of them claims opens the item it cites.
FEATURE_START_LINKS: tuple[StartLink, ...] = tuple(
    link for module in MODULES for link in module.start_links
)


async def _world(session: AsyncSession) -> World:
    """Safwa's answer to what a proposal is made against: the workspace row."""
    workspace = await require_workspace(session)
    return World(revision=workspace.revision, timezone=workspace.timezone)


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
        handlers=handlers,
        presenters=presenters,
        tools=tools,
        views=ALLOWED_VIEWS,
        world=_world,
    )


PROPOSALS: ProposalRegistry = _proposals()


def _autoapprovals() -> dict[tuple[str, str], AutoApprovalRule]:
    rules: dict[tuple[str, str], AutoApprovalRule] = {}
    for module in MODULES:
        for contribution in module.proposals:
            entity = contribution.handler.entity
            rules.update(
                ((entity, action), rule)
                for action, rule in contribution.autoapprovals.items()
            )
    return rules


# The whole of what may be saved without the owner seeing it. A feature that declares
# nothing has nothing here, and every other change takes the review screen.
AUTOAPPROVALS: dict[tuple[str, str], AutoApprovalRule] = _autoapprovals()


def _with_catalogue(agent: AgentSpec) -> AgentSpec:
    """Fill a subagent's `{views}` in, once, so what it reads is what the snapshot hashes."""
    if not agent.views:
        return agent
    if "{views}" not in agent.instructions:
        raise RuntimeError(f"The {agent.name} subagent names views but has no {{views}} to fill")
    return replace(
        agent,
        instructions=agent.instructions.replace(
            "{views}", view_catalogue(AI_VIEWS, agent.views)
        ),
    )


AGENTS = tuple(_with_catalogue(agent) for module in MODULES for agent in module.agents)


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


# The routing rules are prose in the prompt, so a subagent they omit is never routed to,
# and so is a view the Advisor's own list leaves out.
SYSTEM_PROMPT: str = SYSTEM_PROMPT_TEMPLATE.replace(
    "{routes}", _routing_rules()
).replace("{views}", view_catalogue(AI_VIEWS, ADVISOR_VIEWS))

# The one helper, which is not a subagent: it is called rather than routed, so it is not
# in `AGENTS` and no routing rule ever names it. A second helper is what earns a field on
# `FeatureModule`; one does not.
HEAVY_ANALYZER_PROMPT: str = heavy_analyzer.PROMPT_TEMPLATE.replace(
    "{views}", view_catalogue(AI_VIEWS, heavy_analyzer.VIEWS)
)

RECOVERY_HOOKS: tuple[Callable[..., Awaitable[None]], ...] = tuple(
    module.recover for module in MODULES if module.recover is not None
)

# The Cue poll is not a feature's: it delivers whatever any of them wrote.
BACKGROUND_TASKS: tuple[BackgroundTask, ...] = (
    CUE_QUEUE,
    *(task for module in MODULES for task in module.background),
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
            workspace_state=agent.workspace_state,
            clock=agent.clock(context) if agent.clock else None,
        )
        for agent in AGENTS
    )
