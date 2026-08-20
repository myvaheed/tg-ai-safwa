from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from safwa.foundation import state_flow
from safwa.foundation.state_flow import MutableStateFlow, StateFlow


@dataclass(frozen=True, slots=True)
class Progress:
    done: int


class Reader:
    """One consumer of a stream, holding a single request in flight.

    Cancelling a pending `anext` ends the generator, so `quiet()` shields its task instead
    of cancelling it: asserting that nothing arrived must not be what stops the stream.
    """

    def __init__(self, iterator):
        self._iterator = iterator
        self._pending = asyncio.ensure_future(anext(iterator))

    async def next(self, timeout: float = 1.0):
        value = await asyncio.wait_for(asyncio.shield(self._pending), timeout)
        self._pending = asyncio.ensure_future(anext(self._iterator))
        return value

    async def quiet(self, timeout: float = 0.05) -> None:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(self._pending), timeout)

    async def close(self) -> None:
        self._pending.cancel()
        await asyncio.gather(self._pending, return_exceptions=True)
        await self._iterator.aclose()


async def test_current_is_readable_without_waiting():
    flow = MutableStateFlow(Progress(0))

    assert flow.current == Progress(0)
    flow.emit(Progress(1))
    assert flow.current == Progress(1)


async def test_subscriber_receives_current_before_any_update():
    flow = MutableStateFlow(Progress(7))
    reader = Reader(flow.states())
    try:
        assert await reader.next() == Progress(7)
    finally:
        await reader.close()


async def test_slow_subscriber_receives_the_latest_state_not_a_backlog():
    flow = MutableStateFlow(Progress(0))
    reader = Reader(flow.states())
    try:
        assert await reader.next() == Progress(0)

        flow.emit(Progress(1))
        flow.emit(Progress(2))
        flow.emit(Progress(3))

        assert await reader.next() == Progress(3)
        await reader.quiet()
    finally:
        await reader.close()


async def test_equal_state_is_not_republished():
    flow = MutableStateFlow(Progress(0))
    reader = Reader(flow.states())
    try:
        assert await reader.next() == Progress(0)

        flow.emit(Progress(0))

        await reader.quiet()
    finally:
        await reader.close()


async def test_every_subscriber_sees_the_update():
    flow = MutableStateFlow(Progress(0))
    first, second = Reader(flow.states()), Reader(flow.states())
    try:
        await first.next()
        await second.next()

        flow.emit(Progress(5))

        assert await first.next() == Progress(5)
        assert await second.next() == Progress(5)
    finally:
        await first.close()
        await second.close()


async def test_closing_a_subscription_removes_it():
    flow = MutableStateFlow(Progress(0))
    reader = Reader(flow.states())
    await reader.next()
    assert len(flow._subscribers) == 1

    await reader.close()

    assert flow._subscribers == set()


async def test_the_published_view_cannot_write():
    flow = MutableStateFlow(Progress(0))

    view = flow.as_state_flow()

    assert isinstance(view, StateFlow)
    assert not hasattr(view, "emit")
    assert view.current == Progress(0)
    flow.emit(Progress(1))
    assert view.current == Progress(1)


async def test_the_view_subscribes_to_the_same_source():
    flow = MutableStateFlow(Progress(0))
    reader = Reader(flow.as_state_flow().states())
    try:
        assert await reader.next() == Progress(0)
        flow.emit(Progress(2))
        assert await reader.next() == Progress(2)
    finally:
        await reader.close()


# ------------------------------------------------------------------------ operators


async def test_map_derives_current_without_subscribing():
    flow = MutableStateFlow(Progress(3))

    doubled = state_flow.map(flow.as_state_flow(), lambda progress: progress.done * 2)

    assert doubled.current == 6
    assert flow._subscribers == set()
    flow.emit(Progress(4))
    assert doubled.current == 8


async def test_map_publishes_the_transformed_state():
    flow = MutableStateFlow(Progress(0))
    reader = Reader(state_flow.map(flow.as_state_flow(), lambda p: p.done).states())
    try:
        assert await reader.next() == 0

        flow.emit(Progress(1))

        assert await reader.next() == 1
    finally:
        await reader.close()


async def test_map_suppresses_states_that_transform_to_the_same_value():
    flow = MutableStateFlow(Progress(0))
    busy = state_flow.map(flow.as_state_flow(), lambda progress: progress.done > 0)
    reader = Reader(busy.states())
    try:
        assert await reader.next() is False

        flow.emit(Progress(1))
        assert await reader.next() is True

        # A second distinct source state that means the same thing is not an update.
        flow.emit(Progress(2))
        await reader.quiet()
    finally:
        await reader.close()


async def test_map_releases_the_source_subscription_when_closed():
    flow = MutableStateFlow(Progress(0))
    reader = Reader(state_flow.map(flow.as_state_flow(), lambda p: p.done).states())
    await reader.next()
    assert len(flow._subscribers) == 1

    await reader.close()

    assert flow._subscribers == set()


async def test_filter_yields_only_the_states_that_match():
    flow = MutableStateFlow(Progress(0))
    reader = Reader(state_flow.filter(flow.as_state_flow(), lambda p: p.done % 2 == 0))
    try:
        assert await reader.next() == Progress(0)

        flow.emit(Progress(1))
        flow.emit(Progress(2))

        assert await reader.next() == Progress(2)
    finally:
        await reader.close()


async def test_filter_is_a_stream_and_has_no_current():
    flow = MutableStateFlow(Progress(0))
    states = state_flow.filter(flow.as_state_flow(), lambda progress: progress.done > 0)
    reader = Reader(states)
    try:
        assert not hasattr(states, "current")
        await reader.quiet()
    finally:
        await reader.close()


async def test_filter_releases_the_source_subscription_when_closed():
    flow = MutableStateFlow(Progress(0))
    reader = Reader(state_flow.filter(flow.as_state_flow(), lambda progress: True))
    await reader.next()
    assert len(flow._subscribers) == 1

    await reader.close()

    assert flow._subscribers == set()


async def test_merge_opens_with_every_current_and_then_follows_both():
    first, second = MutableStateFlow(Progress(1)), MutableStateFlow(Progress(2))
    reader = Reader(state_flow.merge(first.as_state_flow(), second.as_state_flow()))
    try:
        assert {await reader.next(), await reader.next()} == {Progress(1), Progress(2)}

        second.emit(Progress(9))
        assert await reader.next() == Progress(9)

        first.emit(Progress(8))
        assert await reader.next() == Progress(8)
    finally:
        await reader.close()


async def test_merge_releases_every_source_subscription_when_closed():
    first, second = MutableStateFlow(Progress(0)), MutableStateFlow(Progress(0))
    reader = Reader(state_flow.merge(first.as_state_flow(), second.as_state_flow()))
    await reader.next()
    await reader.next()
    assert len(first._subscribers) == 1
    assert len(second._subscribers) == 1

    await reader.close()

    assert first._subscribers == set()
    assert second._subscribers == set()


async def test_combine_has_a_current_because_both_sides_do():
    first, second = MutableStateFlow(Progress(2)), MutableStateFlow(Progress(3))

    total = state_flow.combine(
        first.as_state_flow(), second.as_state_flow(), lambda a, b: a.done + b.done
    )

    assert total.current == 5
    second.emit(Progress(10))
    assert total.current == 12


async def test_combine_updates_when_either_side_moves():
    first, second = MutableStateFlow(Progress(0)), MutableStateFlow(Progress(0))
    total = state_flow.combine(
        first.as_state_flow(), second.as_state_flow(), lambda a, b: a.done + b.done
    )
    reader = Reader(total.states())
    try:
        # Both sides open by reporting where they are; that is one combined value, not two.
        assert await reader.next() == 0
        await reader.quiet()

        first.emit(Progress(1))
        assert await reader.next() == 1

        second.emit(Progress(4))
        assert await reader.next() == 5
    finally:
        await reader.close()


async def test_combine_suppresses_a_move_that_leaves_the_result_unchanged():
    first, second = MutableStateFlow(Progress(2)), MutableStateFlow(Progress(2))
    largest = state_flow.combine(
        first.as_state_flow(), second.as_state_flow(), lambda a, b: max(a.done, b.done)
    )
    reader = Reader(largest.states())
    try:
        assert await reader.next() == 2

        second.emit(Progress(1))

        await reader.quiet()
    finally:
        await reader.close()


async def test_combine_releases_both_source_subscriptions_when_closed():
    first, second = MutableStateFlow(Progress(0)), MutableStateFlow(Progress(0))
    total = state_flow.combine(
        first.as_state_flow(), second.as_state_flow(), lambda a, b: a.done + b.done
    )
    reader = Reader(total.states())
    await reader.next()
    assert len(first._subscribers) == 1
    assert len(second._subscribers) == 1

    await reader.close()

    assert first._subscribers == set()
    assert second._subscribers == set()


async def test_operators_compose():
    first, second = MutableStateFlow(Progress(1)), MutableStateFlow(Progress(1))
    total = state_flow.combine(
        first.as_state_flow(), second.as_state_flow(), lambda a, b: a.done + b.done
    )

    label = state_flow.map(total, lambda done: f"{done} done")

    assert label.current == "2 done"
    reader = Reader(label.states())
    try:
        assert await reader.next() == "2 done"
        first.emit(Progress(4))
        assert await reader.next() == "5 done"
    finally:
        await reader.close()
