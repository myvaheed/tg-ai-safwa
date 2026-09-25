"""Everything an application has, derived from the one list of features it declares.

An application says which features exist and what its root session says; this says what follows
from that list — the view catalogue and its allowlist, the screens, the commands, the
proposal capabilities, the subagent roster, the hooks and the background tasks. Nothing
product-specific is decided here: what a world is, what the persona says and which
features are in the list are all handed in.

`Registry.of` is meant to be called once, at import time of the application's own module
list, so the assembled prompt is built once and the cacheable prefix stays byte-identical
across turns.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import LlmProvider

from .ai.autoapproval import AutoApprovalReviewer, AutoApprovalRule
from .ai.messages import Memory, StateBlocks
from .ai.sql import ReadOnlyQueryRunner, SqlView, view_catalogue
from .ai.subagents import RoutedSubagent
from .ai.tools import IMMEDIATE_TOOLS, AfterTool, BeforeTool, HelperPort
from .cues.module import CUE_QUEUE, HOOK_TICKS
from .foundation.screens import ScreenCatalogue, ScreenSpec
from .hooks.contracts import HookPolicy, HookSpec, every_switch_on
from .hooks.registry import HookRegistry
from .proposals.api import (
    MutationToolSpec,
    ProposalHandler,
    ProposalPresenter,
    ProposalRegistry,
    SimilarItems,
    WorldReader,
)
from .proposals.store import ProposalStore
from .session import RootSession
from .telegram.contributions import (
    HOME_NAV,
    ScreenCommand,
    StartLink,
    TextInputFlow,
)
from .telegram.manifest import (
    AgentContext,
    AgentSpec,
    BackgroundTask,
    FeatureModule,
    HelperSpec,
)
from .telegram.services import CallbackHandler

RecoveryHook = Callable[[AsyncSession], Awaitable[None]]


def _with_catalogue[Spec: (AgentSpec, HelperSpec)](
    spec: Spec, views: tuple[SqlView, ...]
) -> Spec:
    """Fill a prompt's `{views}` in, once, so what it reads is what the snapshot hashes.

    `views` is the reader's scope either way: a prompt that spells its own list out, with
    columns trimmed on purpose, keeps what it wrote, and its reads are refused against the
    same declaration. A subagent may declare none, and then reads nothing; a helper is
    nothing but a reader, so one with no views is a wiring error.
    """
    if not spec.views and (isinstance(spec, HelperSpec) or "{views}" in spec.instructions):
        raise RuntimeError(f"The {spec.name} reader declares no views, so it can read nothing")
    if "{views}" not in spec.instructions:
        return spec
    return replace(
        spec, instructions=spec.instructions.replace("{views}", view_catalogue(views, spec.views))
    )


@dataclass(frozen=True, slots=True)
class Registry:
    """One application's whole surface, in the shape each part of the shell reads it."""

    modules: tuple[FeatureModule, ...]

    views: tuple[SqlView, ...]
    allowed_views: frozenset[str]

    screens: ScreenCatalogue
    commands: tuple[ScreenCommand, ...]
    callback_actions: Mapping[str, CallbackHandler]
    text_inputs: Mapping[str, TextInputFlow]
    start_links: tuple[StartLink, ...]

    proposals: ProposalRegistry
    autoapprovals: Mapping[tuple[str, str], AutoApprovalRule]
    agents: tuple[AgentSpec, ...]
    helpers: Mapping[str, HelperSpec]
    before_tool: tuple[BeforeTool, ...]
    after_tool: tuple[AfterTool, ...]

    hooks: HookRegistry
    recovery: tuple[RecoveryHook, ...]
    background: tuple[BackgroundTask, ...]

    @classmethod
    def of(
        cls, modules: tuple[FeatureModule, ...], *, world: WorldReader,
        hooks: tuple[HookSpec, ...] = (), hook_policy: HookPolicy = every_switch_on,
    ) -> Registry:
        """Everything the list implies, with each collision refused where it happens.

        `hook_policy` is the application's answer to whether a hook with a switch is on,
        read where the hook is about to work, never copied.
        """
        views = _views(modules)
        allowed = frozenset(view.name for view in views)
        proposals, autoapprovals = _proposals(modules, allowed, world)
        helpers = _helpers(modules, views)
        return cls(
            modules=modules,
            views=views,
            allowed_views=allowed,
            screens=_screens(modules),
            commands=_commands(modules),
            callback_actions=_callback_actions(modules),
            text_inputs=_text_inputs(modules),
            # Tried in registration order; a payload none of them claims opens the item it cites.
            start_links=tuple(link for module in modules for link in module.start_links),
            proposals=proposals,
            autoapprovals=autoapprovals,
            agents=tuple(
                _with_catalogue(agent, views) for module in modules for agent in module.agents
            ),
            helpers=helpers,
            before_tool=tuple(watch for module in modules for watch in module.before_tool),
            after_tool=tuple(watch for module in modules for watch in module.after_tool),
            hooks=HookRegistry.of(
                hooks,
                owners=frozenset(module.name for module in modules),
                helpers=frozenset(helpers),
                # route is answered by agent_runtime before ToolAdapters is reached.
                tools=IMMEDIATE_TOOLS - {"route"},
                policy=hook_policy,
            ),
            recovery=tuple(
                module.recover for module in modules if module.recover is not None
            ),
            # The Cue poll is not a feature's: it delivers whatever any of them wrote. Nor
            # is the tick poll: it hands the hour to whichever hook declared it.
            background=(
                CUE_QUEUE, HOOK_TICKS, *(task for module in modules for task in module.background)
            ),
        )

    def routes(self, line: Callable[[AgentSpec], str]) -> str:
        """The routing rules a root prompt carries, one line per declared subagent.

        A subagent no line names is one nothing routes to, so this is built from the
        roster rather than written out beside it.
        """
        unknown = {
            name
            for agent in self.agents
            for name in agent.mutation_tools
            if name not in self.proposals.tools
        }
        if unknown:
            raise RuntimeError(f"A subagent declares tools no feature publishes: {sorted(unknown)}")
        return "\n".join(line(agent) for agent in self.agents)

    def subagents(
        self, context: AgentContext, *, prompt: Callable[[AgentSpec], str]
    ) -> tuple[RoutedSubagent, ...]:
        """Bind every declared subagent to this application's read tools and clock."""
        return tuple(agent.bind(context, prompt=prompt(agent)) for agent in self.agents)

    def helper_ports(
        self, provider: LlmProvider, query_runner: ReadOnlyQueryRunner
    ) -> dict[str, HelperPort]:
        """Every declared helper as a mini session, each scoped to its own view list."""
        return {
            name: HelperPort(
                run=spec.build(
                    provider, query_runner.scoped(spec.views), prompt=spec.instructions
                ),
            )
            for name, spec in self.helpers.items()
        }

    def root_session(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: LlmProvider,
        memory: Memory,
        query_runner: ReadOnlyQueryRunner,
        *,
        views: tuple[str, ...],
        workspace_state: Callable[[AsyncSession], Awaitable[StateBlocks]],
        system_prompt: str,
        model_name: str,
        provider_name: str = "openai-compatible",
        cache_breakpoints: bool = False,
        subagents: tuple[RoutedSubagent, ...] = (),
        helpers: Mapping[str, HelperPort] | None = None,
        autoapprove: bool = True,
        reviews: ProposalStore | None = None,
    ) -> RootSession:
        """The one session that writes to the chat, carrying what the features declared.

        `views` is that session's own list, and it is scoped here rather than by the caller,
        so the reader that answers is the reader its declaration describes.
        """
        return RootSession(
            sessions,
            provider,
            memory,
            query_runner.scoped(views),
            self.proposals,
            screens=self.screens,
            workspace_state=workspace_state,
            system_prompt=system_prompt,
            model_name=model_name,
            provider_name=provider_name,
            cache_breakpoints=cache_breakpoints,
            autoapproval=(
                AutoApprovalReviewer(provider, self.autoapprovals) if autoapprove else None
            ),
            subagents=subagents,
            helpers=helpers,
            before_tool=self.before_tool,
            after_tool=self.after_tool,
            hooks=self.hooks,
            reviews=reviews,
        )


def _views(modules: tuple[FeatureModule, ...]) -> tuple[SqlView, ...]:
    collected: dict[str, SqlView] = {}
    for module in modules:
        for view in module.views:
            if view.name in collected:
                raise RuntimeError(f"Two features publish the view {view.name}")
            collected[view.name] = view
    return tuple(collected.values())


def _screens(modules: tuple[FeatureModule, ...]) -> ScreenCatalogue:
    collected: list[ScreenSpec] = []
    seen: set[str] = set()
    for module in modules:
        for spec in module.screens:
            if spec.item_type in seen:
                raise RuntimeError(f"Two features publish the {spec.item_type} screen")
            seen.add(spec.item_type)
            collected.append(spec)
    return ScreenCatalogue.of(tuple(collected))


def _commands(modules: tuple[FeatureModule, ...]) -> tuple[ScreenCommand, ...]:
    """The screens reached by name, in registration order, which is Telegram's order.

    Exactly one of them is the home screen: `go_back` and every menu button lead there,
    so an application without one has buttons that reach nothing.
    """
    commands = tuple(command for module in modules for command in module.commands)
    home = [command for command in commands if command.nav == HOME_NAV]
    if len(home) != 1:
        raise RuntimeError(
            f"Exactly one screen must declare nav={HOME_NAV!r}; {len(home)} do"
        )
    return commands


def _callback_actions(modules: tuple[FeatureModule, ...]) -> dict[str, CallbackHandler]:
    actions: dict[str, CallbackHandler] = {}
    for module in modules:
        for name, handler in module.callback_actions.items():
            if name in actions:
                raise RuntimeError(f"Two features answer the callback {name}")
            actions[name] = handler
    return actions


def _text_inputs(modules: tuple[FeatureModule, ...]) -> dict[str, TextInputFlow]:
    flows: dict[str, TextInputFlow] = {}
    for module in modules:
        for flow in module.text_inputs:
            if flow.name in flows:
                raise RuntimeError(f"Two features answer the {flow.name} text input")
            flows[flow.name] = flow
    return flows


def _proposals(
    modules: tuple[FeatureModule, ...], views: frozenset[str], world: WorldReader
) -> tuple[ProposalRegistry, dict[tuple[str, str], AutoApprovalRule]]:
    handlers: dict[str, ProposalHandler] = {}
    presenters: dict[str, ProposalPresenter] = {}
    tools: dict[str, MutationToolSpec] = {}
    similar: dict[str, SimilarItems] = {}
    # The whole of what may be saved without the owner seeing it. A feature that declares
    # nothing has nothing here, and every other change takes the review screen.
    rules: dict[tuple[str, str], AutoApprovalRule] = {}

    def register_tool(tool: MutationToolSpec) -> None:
        if tool.name in tools:
            raise RuntimeError(f"Two features publish the mutation tool {tool.name}")
        tools[tool.name] = tool

    for module in modules:
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
            if contribution.similar is not None:
                similar[entity] = contribution.similar
            rules.update(
                ((entity, action), rule)
                for action, rule in contribution.autoapprovals.items()
            )
        for tool in module.mutation_tools:
            register_tool(tool)
    registry = ProposalRegistry(
        handlers=handlers, presenters=presenters, tools=tools, views=views, world=world,
        similar=similar,
    )
    return registry, rules


def _helpers(
    modules: tuple[FeatureModule, ...], views: tuple[SqlView, ...]
) -> dict[str, HelperSpec]:
    """A helper is called rather than routed, so it is in no routing rule and no roster."""
    helpers: dict[str, HelperSpec] = {}
    for module in modules:
        for helper in module.helpers:
            if helper.name in helpers:
                raise RuntimeError(f"Two features publish the {helper.name} helper")
            helpers[helper.name] = _with_catalogue(helper, views)
    return helpers
