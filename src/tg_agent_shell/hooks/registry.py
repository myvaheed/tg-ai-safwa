"""A validated, immutable catalogue, indexed by the boundary that emits each event."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .contracts import (
    AfterTool,
    AfterTurn,
    HookRegistration,
    HookSpec,
    OfferTool,
    OnAfterTool,
    OnAfterTurn,
    Run,
)


@dataclass(frozen=True, slots=True)
class HookEvaluation:
    spec: HookSpec
    payloads: tuple[Any, ...] = ()
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class HookRegistry:
    registrations: tuple[HookRegistration, ...]
    _index: Mapping[type, tuple[HookSpec, ...]]

    @classmethod
    def of(
        cls,
        registrations: tuple[HookRegistration, ...] = (),
        *,
        owners: frozenset[str] = frozenset(),
        helpers: frozenset[str] = frozenset(),
        tools: frozenset[str] = frozenset(),
    ) -> HookRegistry:
        names: set[str] = set()
        index: dict[type, list[HookSpec]] = {}
        for registration in registrations:
            spec = registration.spec
            if not spec.name.strip() or spec.name in names:
                raise RuntimeError(f"Duplicate or empty hook name: {spec.name!r}")
            names.add(spec.name)
            if spec.owner not in owners:
                raise RuntimeError(f"Hook {spec.name} has an unknown owner: {spec.owner}")
            if not spec.on or len(set(spec.on)) != len(spec.on):
                raise RuntimeError(f"Hook {spec.name} needs distinct subscriptions")
            if isinstance(spec.effect, OfferTool) and spec.effect.helper not in helpers:
                raise RuntimeError(f"Hook {spec.name} names an unknown helper: {spec.effect.helper}")
            for subscription in spec.on:
                match subscription, spec.effect:
                    case OnAfterTurn(source=source), Run() if source in {"owner", "system"}:
                        event_type = AfterTurn
                    case OnAfterTool(tool=tool, agent="root", outcome="success"), OfferTool():
                        if tool not in tools:
                            raise RuntimeError(f"Hook {spec.name} names an unavailable tool boundary: {tool}")
                        event_type = AfterTool
                    case _:
                        raise RuntimeError(f"Hook {spec.name} has an incompatible subscription/effect")
                if registration.enabled:
                    bucket = index.setdefault(event_type, [])
                    if spec not in bucket:
                        bucket.append(spec)
        return cls(registrations, MappingProxyType({key: tuple(value) for key, value in index.items()}))

    def listens(self, event_type: type) -> bool:
        return event_type in self._index

    async def evaluate(self, event: AfterTurn | AfterTool) -> AsyncIterator[HookEvaluation]:
        """Evaluate only enabled matches, once per hook, without performing effects.

        Both current effects are optional assistance. Their failures are reported to
        the adapter, independently, and cancellation always propagates.
        """
        subscription_type = OnAfterTurn if isinstance(event, AfterTurn) else OnAfterTool
        for spec in self._index.get(type(event), ()):
            if not any(
                isinstance(on, subscription_type) and on.matches(event) for on in spec.on
            ):
                continue
            try:
                payloads = tuple(await spec.evaluate(event))
            except Exception as error:
                yield HookEvaluation(spec, error=error)
            else:
                yield HookEvaluation(spec, payloads)
