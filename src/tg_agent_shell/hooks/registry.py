"""A validated, immutable catalogue, indexed by the boundary that emits each event."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..foundation.changes import Committed
from .contracts import (
    Advise,
    AfterTool,
    AfterTurn,
    BeforeTool,
    HookPolicy,
    HookSpec,
    OfferTool,
    OnAfterTool,
    OnAfterTurn,
    OnBeforeTool,
    OnCommitted,
    OnTick,
    RefuseTool,
    Run,
    Tick,
    TickTime,
    every_switch_on,
)

HookEvent = AfterTurn | AfterTool | BeforeTool | Committed | Tick

# Which subscription reads which event; the registry's compatibility rules are below.
_SUBSCRIPTION_FOR: Mapping[type, type] = MappingProxyType({
    AfterTurn: OnAfterTurn, AfterTool: OnAfterTool, BeforeTool: OnBeforeTool,
    Committed: OnCommitted, Tick: OnTick,
})


@dataclass(frozen=True, slots=True)
class HookEvaluation:
    spec: HookSpec
    payloads: tuple[Any, ...] = ()
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class HookRegistry:
    specs: tuple[HookSpec, ...]
    policy: HookPolicy
    _index: Mapping[type, tuple[HookSpec, ...]]

    @classmethod
    def of(
        cls,
        specs: tuple[HookSpec, ...] = (),
        *,
        owners: frozenset[str] = frozenset(),
        helpers: frozenset[str] = frozenset(),
        tools: frozenset[str] = frozenset(),
        policy: HookPolicy = every_switch_on,
    ) -> HookRegistry:
        names: set[str] = set()
        index: dict[type, list[HookSpec]] = {}
        for spec in specs:
            if not spec.name.strip() or spec.name in names:
                raise RuntimeError(f"Duplicate or empty hook name: {spec.name!r}")
            names.add(spec.name)
            if spec.owner not in owners:
                raise RuntimeError(f"Hook {spec.name} has an unknown owner: {spec.owner}")
            if not spec.on or len(set(spec.on)) != len(spec.on):
                raise RuntimeError(f"Hook {spec.name} needs distinct subscriptions")
            if isinstance(spec.effect, OfferTool | RefuseTool) and spec.effect.helper not in helpers:
                raise RuntimeError(f"Hook {spec.name} names an unknown helper: {spec.effect.helper}")
            for subscription in spec.on:
                match subscription, spec.effect:
                    case OnAfterTurn(source=source), Run() if source in {"owner", "system"}:
                        event_type: type = AfterTurn
                    case OnAfterTool(tool=tool, agent="root", outcome="success"), OfferTool():
                        if tool not in tools:
                            raise RuntimeError(f"Hook {spec.name} names an unavailable tool boundary: {tool}")
                        event_type = AfterTool
                    case OnBeforeTool(tool=tool, agent="root"), RefuseTool():
                        if tool not in tools:
                            raise RuntimeError(f"Hook {spec.name} names an unavailable tool boundary: {tool}")
                        event_type = BeforeTool
                    case OnCommitted(kind=kind), Advise() if kind.strip():
                        event_type = Committed
                    case OnTick(at=at), Advise() | Run() if callable(at):
                        event_type = Tick
                    case _:
                        raise RuntimeError(f"Hook {spec.name} has an incompatible subscription/effect")
                bucket = index.setdefault(event_type, [])
                if spec not in bucket:
                    bucket.append(spec)
        return cls(specs, policy, MappingProxyType({key: tuple(value) for key, value in index.items()}))

    @property
    def daily_clocks(self) -> tuple[TickTime, ...]:
        """Each reader of a daily time once, in catalogue order: what one look reads."""
        clocks: list[TickTime] = []
        for spec in self.specs:
            for subscription in spec.on:
                if isinstance(subscription, OnTick) and subscription.at not in clocks:
                    clocks.append(subscription.at)
        return tuple(clocks)

    @property
    def agent_related(self) -> tuple[HookSpec, ...]:
        """The hooks the owner may turn off, in catalogue order."""
        return tuple(spec for spec in self.specs if spec.agent_related)

    def listens(self, event_type: type) -> bool:
        return event_type in self._index

    async def switched_on(
        self, sessions: async_sessionmaker[AsyncSession], spec: HookSpec
    ) -> bool:
        """Read the policy now, so a switch the owner just turned counts without a restart."""
        if not spec.agent_related:
            return True
        async with sessions() as session:
            return await self.policy(session, spec.name)

    async def evaluate(
        self, event: HookEvent, sessions: async_sessionmaker[AsyncSession]
    ) -> AsyncIterator[HookEvaluation]:
        """Evaluate only matches that are switched on, once per hook, without performing effects.

        Every current effect is optional assistance. Their failures are reported to the
        adapter, independently, and cancellation always propagates.
        """
        subscription_type = _SUBSCRIPTION_FOR[type(event)]
        for spec in self._index.get(type(event), ()):
            if not any(
                isinstance(on, subscription_type) and on.matches(event) for on in spec.on
            ):
                continue
            if not await self.switched_on(sessions, spec):
                continue
            try:
                payloads = tuple(await spec.evaluate(event))
            except Exception as error:
                yield HookEvaluation(spec, error=error)
            else:
                yield HookEvaluation(spec, payloads)

    async def prepare(
        self, sessions: async_sessionmaker[AsyncSession], name: str, items: Sequence[Any]
    ) -> str | None:
        """The words of a hook's pending request, or None when there is nothing to say.

        Nothing is said for a hook that is gone, switched off, or whose feature finds none
        of the items still worth asking about. A feature that fails to say raises: that is
        not nothing to say, and the request stays owed for the poll after.
        """
        spec = next((spec for spec in self.specs if spec.name == name), None)
        if spec is None or not isinstance(spec.effect, Advise):
            return None
        if not await self.switched_on(sessions, spec):
            return None
        async with sessions() as session:
            return await spec.effect.prepare(session, items)
