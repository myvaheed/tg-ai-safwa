"""Don't disturb: the Profile's windows the owner's interval Reminders wait out."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from ui_harness import FakeCallback, FakeMessage, services_for

from llm_gateway import CompletionTurn, ToolCall
from safwa.bootstrap.modules import PROPOSALS
from safwa.constants import WEEKDAY_NAMES
from safwa.features.profile.api import dont_disturb
from safwa.features.profile.dont_disturb import DONT_DISTURB_FORMS, DontDisturbReader
from safwa.features.profile.model import DONT_DISTURB_DEFAULT, ProfileField, UserProfile
from safwa.features.profile.proposal import ProfileProposalHandler
from safwa.features.profile.telegram import command_profile
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.reminders.api import resolve, schedule_columns, schedule_payload
from safwa.features.reminders.firing import Firing, settle
from safwa.features.reminders.model import Reminder
from safwa.features.reminders.telegram.review import DONT_DISTURB, ReminderProposalPresenter
from safwa.features.reminders.use_cases import create_reminder, follow_dont_disturb
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.proposals.api import ChangeAction, ProposalChange, ToolPreparationError
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.dialogue import ordinary_text

TZ = ZoneInfo("Europe/Istanbul")  # the test workspace's own zone


def local(hour: int, minute: int = 0, *, day: int = 13) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ).astimezone(UTC)


class HoursProvider:
    """A model that ends the session one way, and counts that it was asked."""

    def __init__(self, name: str = "set_quiet_hours", **arguments: object) -> None:
        self.name = name
        self.arguments = arguments
        self.calls: list[object] = []

    async def complete(self, request):
        self.calls.append(request)
        call = ToolCall(id="hours", name=self.name, arguments_json=json.dumps(self.arguments))
        return CompletionTurn(content="", tool_calls=(call,))


TWO_WINDOWS = HoursProvider(windows=["22:00-08:00", "13:00-14:00"])


async def _own_interval(session, *, start: datetime, next_fire: datetime) -> Reminder:
    """An interval Reminder of the owner's, every 2 hours from `start`, due at `next_fire`."""
    schedule = resolve(interval_minutes=120, now=start, tz=TZ)
    reminder = Reminder(remind_text="Drink water.", next_fire_at=next_fire, **schedule_columns(schedule))
    session.add(reminder)
    await session.flush()
    return reminder


async def _settled(session, reminder: Reminder, due: datetime) -> datetime:
    firing = Firing(reminder.id, reminder.remind_text, "every 2 hours", due)
    await settle(session, [firing], now=due + timedelta(seconds=30), tz=TZ)
    return reminder.next_fire_at


async def test_a_new_workspace_waits_out_22_to_8(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    assert DONT_DISTURB_DEFAULT == ("22:00-08:00",)
    async with sessions() as session:
        assert (await session.get(UserProfile, 1)).dont_disturb == list(DONT_DISTURB_DEFAULT)
        assert await dont_disturb(session) == list(DONT_DISTURB_DEFAULT)
    message = FakeMessage(931, bot_message=True, answer_as_new=True)
    await command_profile(message, services_for(sessions))
    text, markup = message.edits[-1]
    assert "<i>Don't disturb:</i> 22:00–08:00" in text
    assert "💤 Don't disturb" in {item.text for row in markup.inline_keyboard for item in row}


async def _send_on_screen(sessions, provider, words: str):
    services = services_for(sessions)
    services.features = SimpleNamespace(dont_disturb=DontDisturbReader(provider))
    message = FakeMessage(932, bot_message=True, answer_as_new=True)
    await command_profile(message, services)
    button = next(
        item
        for row in message.edits[-1][1].inline_keyboard
        for item in row
        if item.text == "💤 Don't disturb"
    )
    await callback_token_handler(FakeCallback(button.callback_data.split(":", 1)[1], message), services)
    answer = FakeMessage(933, text=words, bot_message=False, bot=message.bot)
    await ordinary_text(answer, services)
    return message


async def test_hours_sent_on_the_screen_are_read_into_windows(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    provider = HoursProvider(windows=["22:00-08:00", "13:00-14:00"])
    message = await _send_on_screen(sessions, provider, "22–8 and 13–14")
    assert len(provider.calls) == 1
    async with sessions() as session:
        assert await dont_disturb(session) == ["22:00-08:00", "13:00-14:00"]
    assert "<i>Don't disturb:</i> 22:00–08:00, 13:00–14:00" in message.bot.edits[-1][1]


async def test_off_removes_every_window_without_the_session(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    provider = HoursProvider()
    message = await _send_on_screen(sessions, provider, "off")
    assert provider.calls == []
    async with sessions() as session:
        assert await dont_disturb(session) == []
    assert "<i>Don't disturb:</i> off" in message.bot.edits[-1][1]


async def test_words_without_hours_are_refused_with_the_forms(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    provider = HoursProvider("not_clear_enough", reason="No hours are named.")
    message = await _send_on_screen(sessions, provider, "at night")
    async with sessions() as session:
        assert await dont_disturb(session) == list(DONT_DISTURB_DEFAULT)
    assert f"No hours are named. {DONT_DISTURB_FORMS}" in message.bot.edits[-1][1]


async def test_windows_that_leave_no_time_of_day_are_refused():
    """PS-QUIET-025 — tests/brd/profile.feature"""
    reader = DontDisturbReader(HoursProvider(windows=["08:00-20:00", "20:00-08:00"]))
    with pytest.raises(DomainError, match=DONT_DISTURB_FORMS):
        await reader.windows("all day long")


async def test_windows_that_leave_no_time_of_day_are_never_stored(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    async with sessions() as session:
        with pytest.raises(DomainError, match="no time of day"):
            await set_profile_field(
                session, ProfileField.DONT_DISTURB, ["08:00-20:00", "20:00-08:00"]
            )


async def test_hours_asked_for_in_words_are_read_before_the_screen(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    handler = ProfileProposalHandler()
    change = PROPOSALS.change_from_tool("profile", {"dont_disturb": "22-8 and 13-14"})
    async with sessions() as session:
        context = SimpleNamespace(session=session, provider=TWO_WINDOWS)
        prepared = await handler.prepare(context, change)
        assert prepared.values == {"dont_disturb": ["22:00-08:00", "13:00-14:00"]}
        await handler.apply(
            SimpleNamespace(session=session),
            ProposalChange(entity="profile", action=change.action, values=prepared.values),
        )
        assert await dont_disturb(session) == ["22:00-08:00", "13:00-14:00"]

        unclear = SimpleNamespace(
            session=session, provider=HoursProvider("not_clear_enough", reason="No hours.")
        )
        with pytest.raises(ToolPreparationError, match="dont_disturb"):
            await handler.prepare(unclear, change)


async def test_an_interval_from_20_rings_at_20_and_next_at_8(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    async with sessions() as session:
        await set_profile_field(session, ProfileField.DONT_DISTURB, ["22:00-08:00", "13:00-14:00"])
        reminder = await _own_interval(session, start=local(20), next_fire=local(20))
        assert await _settled(session, reminder, local(20)) == local(8, day=14)


async def test_a_firing_that_would_land_on_13_comes_at_14(sessions):
    """RM-QUIET-007 — tests/brd/reminders.feature"""
    async with sessions() as session:
        await set_profile_field(session, ProfileField.DONT_DISTURB, ["22:00-08:00", "13:00-14:00"])
        reminder = await _own_interval(session, start=local(9), next_fire=local(11))
        assert await _settled(session, reminder, local(11)) == local(14)


async def test_off_moves_the_next_firing_back_to_22(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    async with sessions() as session:
        reminder = await _own_interval(session, start=local(20), next_fire=local(8, day=14))
        (await session.get(UserProfile, 1)).dont_disturb = []
        await follow_dont_disturb(session, now=local(21))
        assert reminder.next_fire_at == local(22)

        (await session.get(UserProfile, 1)).dont_disturb = ["22:00-08:00"]
        await follow_dont_disturb(session, now=local(21))
        assert reminder.next_fire_at == local(8, day=14)


async def test_saving_don_t_disturb_moves_the_reminders_at_once(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
    async with sessions() as session:
        reminder = await _own_interval(session, start=start, next_fire=start + timedelta(days=30))
        await set_profile_field(session, ProfileField.DONT_DISTURB, [])
        assert reminder.next_fire_at == start + timedelta(minutes=120)


async def test_a_reminder_at_a_clock_time_rings_inside_the_windows(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    now = datetime.now(UTC)
    async with sessions() as session:
        reminder = await create_reminder(
            session,
            remind_text="Lights out.",
            schedule=resolve(days=WEEKDAY_NAMES, clock="23:00", now=now, tz=TZ),
            tz=TZ,
        )
        assert f"{reminder.next_fire_at.astimezone(TZ):%H:%M}" == "23:00"


async def test_a_reminder_remind_made_rings_at_its_item_s_moments(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    now = datetime.now(UTC)
    schedule = resolve(interval_minutes=120, clock="23:00", now=now, tz=TZ)
    async with sessions() as session:
        own = await create_reminder(session, remind_text="Own.", schedule=schedule, tz=TZ)
        made = await create_reminder(
            session, remind_text="Made.", schedule=schedule, tz=TZ, item_type="check", item_id=1
        )
        assert f"{made.next_fire_at.astimezone(TZ):%H:%M}" == "23:00"
        assert f"{own.next_fire_at.astimezone(TZ):%H:%M}" == "08:00"


async def test_the_review_screen_of_an_interval_shows_don_t_disturb(sessions):
    """PS-QUIET-025 — tests/brd/profile.feature"""
    now = datetime.now(UTC)

    def proposed(schedule) -> ProposalChange:
        return ProposalChange(
            entity="reminder",
            action=ChangeAction.CREATE,
            values={"remind_text": "Drink water.", "schedule": schedule_payload(schedule)},
        )

    async with sessions() as session:
        interval = await ReminderProposalPresenter().screen(
            session, [proposed(resolve(interval_minutes=120, now=now, tz=TZ))]
        )
        daily = await ReminderProposalPresenter().screen(
            session, [proposed(resolve(days=WEEKDAY_NAMES, clock="09:00", now=now, tz=TZ))]
        )
    shown = {field.label: field.after for field in interval.fields}
    assert shown[DONT_DISTURB] == "22:00–08:00"
    assert DONT_DISTURB not in {field.label for field in daily.fields}
