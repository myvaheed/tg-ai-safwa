"""Observable process state: one writer, a value that is always readable, conflated updates.

A Manager owns the `MutableStateFlow` and hands out only `as_state_flow()`, so "one writer"
is a property of the capability that was handed over rather than a rule someone has to
remember.  States are frozen dataclasses, which is what makes the equality check below
suppress republished duplicates without any extra code.

The operators at the bottom are free functions, so import the module rather than the names:
`map` and `filter` shadow builtins otherwise.

    from ..foundation import state_flow

    stage = state_flow.map(run.state, lambda run: run.stage)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol


class StateSource[S](Protocol):
    """Anything a `StateFlow` can be a view over: a value now, and the values after it."""

    @property
    def current(self) -> S: ...

    def states(self) -> AsyncIterator[S]: ...


class StateFlow[S]:
    """Read-only view: `current` plus a conflated subscription."""

    def __init__(self, source: StateSource[S]) -> None:
        self._source = source

    @property
    def current(self) -> S:
        return self._source.current

    def states(self) -> AsyncIterator[S]:
        return self._source.states()


class MutableStateFlow[S]:
    """The writer, owned by the Manager alone.

    What it guarantees:
    - `current` is available always and without waiting;
    - a new subscriber receives `current` immediately, then updates;
    - a slow subscriber receives the LATEST state, never a queue of stale ones;
    - an equal state is not republished;
    - the writer is one — the Manager the flow belongs to.

    Event-loop confined: a call from another thread is marshalled onto the loop at the
    adapter boundary.
    """

    def __init__(self, initial: S) -> None:
        self._current = initial
        self._subscribers: set[asyncio.Queue[S]] = set()
        self._view = StateFlow(self)

    @property
    def current(self) -> S:
        return self._current

    def as_state_flow(self) -> StateFlow[S]:
        return self._view

    def emit(self, state: S) -> None:
        """Publish a state. For durable processes, call it only after the save committed."""
        if state == self._current:
            return
        self._current = state
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(state)

    async def states(self) -> AsyncIterator[S]:
        queue: asyncio.Queue[S] = asyncio.Queue(maxsize=1)
        queue.put_nowait(self._current)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)


_MISSING: Any = object()


async def _interleave[T](*sources: AsyncIterator[T]) -> AsyncIterator[tuple[int, T]]:
    """Yield `(index, value)` as any of the sources produces, and close them all on exit."""
    iterators = list(sources)
    pending = {
        asyncio.ensure_future(anext(iterator)): index for index, iterator in enumerate(iterators)
    }
    try:
        while pending:
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                index = pending.pop(task)
                try:
                    value = task.result()
                except StopAsyncIteration:
                    continue
                yield index, value
                pending[asyncio.ensure_future(anext(iterators[index]))] = index
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for iterator in iterators:
            await iterator.aclose()


@dataclass(frozen=True, slots=True)
class _Mapped[S, T]:
    source: StateSource[S]
    transform: Callable[[S], T]

    @property
    def current(self) -> T:
        return self.transform(self.source.current)

    async def states(self) -> AsyncIterator[T]:
        upstream = self.source.states()
        previous = _MISSING
        try:
            async for state in upstream:
                value = self.transform(state)
                if value != previous:
                    previous = value
                    yield value
        finally:
            await upstream.aclose()


@dataclass(frozen=True, slots=True)
class _Combined[A, B, T]:
    first: StateSource[A]
    second: StateSource[B]
    transform: Callable[[A, B], T]

    @property
    def current(self) -> T:
        return self.transform(self.first.current, self.second.current)

    async def states(self) -> AsyncIterator[T]:
        latest: list[Any] = [self.first.current, self.second.current]
        merged = _interleave(self.first.states(), self.second.states())
        previous = _MISSING
        try:
            async for index, state in merged:
                latest[index] = state
                value = self.transform(latest[0], latest[1])
                if value != previous:
                    previous = value
                    yield value
        finally:
            await merged.aclose()


def map[S, T](flow: StateSource[S], transform: Callable[[S], T]) -> StateFlow[T]:
    """Derive a flow of `transform(state)`.

    `current` is computed on every read, so `transform` has to be pure and cheap: the
    derived flow holds no subscription of its own until someone iterates it.  A transform
    that maps two source states onto one value publishes that value once, the same way the
    source suppresses a republished duplicate.
    """
    return StateFlow(_Mapped(flow, transform))


def combine[A, B, T](
    first: StateSource[A],
    second: StateSource[B],
    transform: Callable[[A, B], T],
) -> StateFlow[T]:
    """Derive a flow of `transform(a, b)`, updating whenever either side moves.

    Both sides always have a current value, so the combination always has one too. For a
    third source, combine the result again.
    """
    return StateFlow(_Combined(first, second, transform))


def filter[S](flow: StateSource[S], predicate: Callable[[S], bool]) -> AsyncIterator[S]:
    """The states that satisfy `predicate`.

    This is a stream, not a `StateFlow`: if the current state fails the predicate there is
    no current value to answer with, and a `StateFlow` that cannot answer `current` is not
    one.  Read it with `async for`.
    """

    async def stream() -> AsyncIterator[S]:
        upstream = flow.states()
        try:
            async for state in upstream:
                if predicate(state):
                    yield state
        finally:
            await upstream.aclose()

    return stream()


def merge[S](*flows: StateSource[S]) -> AsyncIterator[S]:
    """Every state from every source, in the order they arrive.

    A stream for the same reason `filter` is one: several sources have several current
    values and no single one. Each source opens by reporting where it already is, so the
    first values out are the currents.
    """

    async def stream() -> AsyncIterator[S]:
        merged = _interleave(*[flow.states() for flow in flows])
        try:
            async for _, state in merged:
                yield state
        finally:
            await merged.aclose()

    return stream()
