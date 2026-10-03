"""Life in weeks: a square for every week of the owner's life, coloured by what was recorded."""

from __future__ import annotations

import json
import warnings
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO

import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PIL import Image
from schedule_helpers import create_card as create_scheduled_card
from sqlalchemy import select, update
from telegram_fakes import QueueTestMessage
from ui_harness import FakeCallback, FakeMessage, services_for

import safwa.features.life.agent as life_agent
from llm_gateway import ToolCall
from safwa.features.cards.model import Card
from safwa.features.cards.telegram import CATEGORY_COLORS
from safwa.features.cards.use_cases import create_card, finish_action
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.life.charts import LIFE_ALBUM, Span, draw_life
from safwa.features.life.measures import (
    FEELING_STOPS,
    LIFE_TREND_WEEKS,
    LIFE_VALUES_SHOWN,
    MET_COLORS,
    OTHER_COLOR,
    RUNNING_COLOR,
    LifeChart,
    blend,
    focuses,
    offered,
    picture,
    tint,
)
from safwa.features.life.model import (
    LIFE_YEARS_DEFAULT,
    LIFE_YEARS_MAX,
    LIFE_YEARS_MIN,
    LifeSettings,
)
from safwa.features.life.records import (
    LifeAction,
    LifeRecords,
    SprintSpan,
    life_records,
    local_today,
)
from safwa.features.life.telegram import render_settings
from safwa.features.life.use_cases import set_birth_date
from safwa.features.life.weeks import LIFE_WEEKS, LifeGrid, heading, month_starts
from safwa.features.retro.telegram import render_retro_list
from safwa.features.values.use_cases import create_value
from safwa.foundation.charts import MONTHS, SURFACE
from safwa.foundation.workspace import Workspace
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.telegram import callback_token_handler, dismiss_prior_ui
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.manifest import AgentContext
from tg_agent_shell.telegram.model import UiSession

BORN = date(1992, 3, 14)
GRID = LifeGrid(BORN, LIFE_YEARS_DEFAULT)
# A Friday. Its week began on Saturday 26 September, as every week of the owner's does.
TODAY = date(2026, 10, 2)
SINCE = date(2026, 8, 8)


def _records(**given) -> LifeRecords:
    return LifeRecords(
        **{
            "today": TODAY,
            "since": SINCE,
            "feelings": {},
            "actions": (),
            "sprints": (),
            "values": (),
            "effort_tracking": False,
            **given,
        }
    )


def _action(
    day: date,
    effort: float | None = None,
    categories: tuple[str, ...] = (),
    energy: tuple[str, ...] = (),
    values: tuple[str, ...] = (),
) -> LifeAction:
    return LifeAction(day, effort, frozenset(categories), frozenset(energy), frozenset(values))


# Eight weeks: 8 August to 2 October. Weeks of 8 and 15 August, then 19 and 26 September.
ACTIONS = (
    _action(date(2026, 8, 8), 2, ("work",), ("cognitive",), ("Health",)),
    _action(date(2026, 8, 9), None, ("self",), ("physical",)),
    _action(date(2026, 8, 16), 3, ("work",), values=("Health",)),
    _action(date(2026, 8, 16), 1, ("work",)),
    _action(date(2026, 8, 16)),
    _action(date(2026, 9, 20), None, ("self",), values=("Craft",)),
    _action(date(2026, 9, 27), 5, ("work",), values=("Health",)),
)
FEELINGS = {
    date(2026, 8, 8): 3,
    date(2026, 8, 15): 6,
    date(2026, 8, 16): 8,
    date(2026, 9, 19): 5,
    date(2026, 9, 26): 9,
}
SPRINTS = (
    SprintSpan("26.08-01", date(2026, 8, 8), date(2026, 8, 19), True, None),
    SprintSpan("26.08-02", date(2026, 8, 20), date(2026, 9, 18), False, None),
    SprintSpan("26.09-01", date(2026, 9, 19), TODAY, None, (14, 14)),
)
RECORDS = _records(
    feelings=FEELINGS,
    actions=ACTIONS,
    sprints=SPRINTS,
    values=("Craft", "Family", "Health"),
    effort_tracking=True,
)


def _week(day: date):
    return GRID.cell(day)


async def _tap(message: QueueTestMessage, services, text: str) -> None:
    """Press the button with this text on the screen the chat shows last."""
    [button] = [
        button
        for row in message.markups[-1].inline_keyboard
        for button in row
        if button.text == text
    ]
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )


async def _press(message: FakeMessage, services, markup, text: str):
    [button] = [button for row in markup.inline_keyboard for button in row if button.text == text]
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )


# ------------------------------------------------------------------------------ the way in


async def test_lf_open_001_life_in_weeks_is_one_tap_from_the_retro_list(sessions) -> None:
    """LF-OPEN-001 — tests/brd/life.feature"""
    services = services_for(sessions)
    listed = QueueTestMessage(message_id=900, answer_as_new=True)
    # No Sprint has ended, and the way in is there all the same.
    await render_retro_list(listed, services)
    assert "No Sprint has ended yet" in listed.rendered[-1]
    assert "⏳ Life in weeks" in listed.buttons()

    # No birth date: the screen asks for one and offers no picture.
    await _tap(listed, services, "⏳ Life in weeks")
    assert "Set your birth date in ⚙️ Settings" in listed.rendered[-1]
    assert listed.buttons() == ["⚙️ Settings", "↩️ Back", "↩️ Menu"]
    await _tap(listed, services, "↩️ Back")
    assert listed.rendered[-1].startswith("<b>Retro</b>")

    async with sessions() as session:
        today = await local_today(session, utcnow())
        await set_birth_date(session, BORN, today)
        await create_diary_entry(session, entry_date=today, body="A good day.", feeling_score=8)
        card = await create_card(session, kind="action", title="Write", categories={"work"})
        await finish_action(session, card.id)
        await session.commit()
    await _tap(listed, services, "⏳ Life in weeks")
    screen = listed.rendered[-1]
    assert screen.startswith("<b>⏳ Life in weeks</b>\nAge 34, week ")
    assert f"records since {today.day} {MONTHS[today.month - 1]} {today.year}" in screen
    # A button for each picture with something to draw, and nothing for the rest.
    assert listed.buttons() == [
        "😊 Feeling", "✅ Actions", "🏷 Categories", "⚙️ Settings", "↩️ Back", "↩️ Menu"
    ]


# ------------------------------------------------------------------------------ Settings


async def test_lf_set_002_the_birth_date_and_the_years_are_the_owners_settings(sessions) -> None:
    """LF-SET-002 — tests/brd/life.feature"""
    services = services_for(sessions)
    message = FakeMessage(940, bot_message=True, answer_as_new=True)
    await render_settings(message, services)
    text, markup = message.edits[-1]
    assert "Birth date: not set" in text
    assert f"Years in the grid: {LIFE_YEARS_DEFAULT}" in text
    assert [button.text for row in markup.inline_keyboard for button in row] == [
        "🎂 Birth date", "📏 Years", "↩️ Back", "↩️ Menu"
    ]

    async def typed(value: str, number: int) -> str:
        await ordinary_text(
            FakeMessage(number, text=value, bot_message=False, bot=message.bot), services
        )
        return message.bot.edits[-1][1]

    await _press(message, services, markup, "🎂 Birth date")
    async with sessions() as session:
        tomorrow = await local_today(session, utcnow()) + timedelta(days=1)
    too_old = date(date.today().year - LIFE_YEARS_MAX, 6, 1)
    for refused, why in (
        ("14.03.1992", "Send a date as YYYY-MM-DD"),
        (tomorrow.isoformat(), "A birth date is a day before today"),
        (too_old.isoformat(), f"at most {LIFE_YEARS_MAX} years ago"),
    ):
        assert why in await typed(refused, 941)
    async with sessions() as session:
        assert await session.get(LifeSettings, 1) is None
        ui = await session.scalar(select(UiSession))
        assert (ui.kind, ui.state["field"]) == ("text_input", "birth_date")

    taken = await typed("1992-03-14", 942)
    assert "Birth date updated." in taken and "Birth date: 1992-03-14" in taken

    await _press(message, services, message.bot.edits[-1][2], "📏 Years")
    for refused in (str(LIFE_YEARS_MIN - 1), str(LIFE_YEARS_MAX + 1), "ninety"):
        assert f"from {LIFE_YEARS_MIN} to {LIFE_YEARS_MAX}" in await typed(refused, 943)
    taken = await typed("100", 944)
    assert "Years in the grid updated." in taken and "Years in the grid: 100" in taken
    async with sessions() as session:
        settings = await session.get(LifeSettings, 1)
        assert (settings.birth_date, settings.years) == (BORN, 100)
    back = message.bot.edits[-1][2]
    assert [button.text for row in back.inline_keyboard for button in row][-2:] == [
        "↩️ Back", "↩️ Menu"
    ]


# ------------------------------------------------------------------------------ the grid


def test_lf_grid_003_a_square_for_every_week_of_life_and_a_row_for_every_year() -> None:
    """LF-GRID-003 — tests/brd/life.feature"""
    assert GRID.cell(BORN) == (0, 0)
    assert GRID.cell(BORN + timedelta(days=6)) == (0, 0)
    assert GRID.cell(BORN + timedelta(days=7)) == (0, 1)
    # 1992 to 1993 is 365 days: the 52nd square takes the 51st week's day over.
    assert GRID.cell(date(1993, 3, 13)) == (0, LIFE_WEEKS - 1)
    assert GRID.cell(date(1993, 3, 14)) == (1, 0)
    # The same week of the year in every row.
    assert GRID.cell(date(2026, 9, 26)) == (34, 28)
    assert GRID.start((34, 28)) == date(2026, 9, 26)
    assert GRID.start((5, 28)).strftime("%m-%d") == "09-26"
    # Every month above its column: March begins again 50 weeks and 2 days after 14 March.
    months = dict((name, at) for at, name in month_starts(BORN))
    assert list(months) == list(MONTHS)
    assert months["Mar"] == (date(1993, 3, 1) - BORN).days / 7
    assert months["Apr"] == (date(1992, 4, 1) - BORN).days / 7
    assert dict((name, at) for at, name in month_starts(date(1992, 3, 1)))["Mar"] == 0

    leap = LifeGrid(date(2000, 2, 29), LIFE_YEARS_DEFAULT)
    assert leap.cell(date(2001, 2, 27)) == (0, LIFE_WEEKS - 1)
    assert leap.cell(date(2001, 2, 28)) == (1, 0)

    assert GRID.rows(TODAY) == LIFE_YEARS_DEFAULT
    assert LifeGrid(date(1930, 1, 1), LIFE_YEARS_MIN).rows(TODAY) == 97
    assert heading(GRID, TODAY, SINCE) == (
        "Age 34, week 29 of the year · 1,802 weeks lived · records since 8 Aug 2026"
    )

    # Grey before the records, pale with nothing counted, empty ahead.
    shown = picture(LifeChart.FEELING, RECORDS, GRID)
    span = Span.of(GRID, TODAY, SINCE)
    before = span.look(_week(SINCE - timedelta(days=1)), shown)
    pale = span.look(_week(date(2026, 8, 22)), shown)
    ahead = span.look(_week(TODAY + timedelta(days=7)), shown)
    assert len({before[0], pale[0], ahead[0]}) == 3
    assert before[2] is pale[2] is ahead[2] is None
    assert span.look(_week(TODAY), shown)[2] == shown.paints[_week(TODAY)]
    assert shown.missing == "no feeling in the Diary"

    pictures = draw_life(shown, GRID, TODAY, SINCE)
    assert [name for name, _ in pictures] == ["life", "close_up"]
    taller = draw_life(shown, LifeGrid(BORN, LIFE_YEARS_MAX), TODAY, SINCE)
    heights = [Image.open(BytesIO(png)).height for _, png in (pictures[0], taller[0])]
    assert heights[0] < heights[1]


async def test_lf_grid_003_the_records_begin_with_safwa_or_the_diary_on_local_days(
    sessions,
) -> None:
    """LF-GRID-003 — tests/brd/life.feature"""
    async with sessions() as session:
        today = await local_today(session, utcnow())
        assert (await life_records(session, utcnow())).since == today

        earlier = today - timedelta(days=10)
        await create_diary_entry(session, entry_date=earlier, body="Before Safwa.")
        card = await create_card(session, kind="action", title="Late")
        await finish_action(session, card.id)
        # 22:30 in UTC is 01:30 the next day in Istanbul.
        late = datetime.combine(today, time(22, 30), tzinfo=UTC)
        await session.execute(update(Card).where(Card.id == card.id).values(completed_at=late))
        await session.commit()
    async with sessions() as session:
        records = await life_records(session, utcnow())
        assert (await session.get(Workspace, 1)).timezone == "Europe/Istanbul"
    assert records.since == earlier
    assert [action.day for action in records.actions] == [today + timedelta(days=1)]


async def test_lf_paint_004_a_repeated_action_counts_each_time_it_was_finished(sessions) -> None:
    """LF-PAINT-004 — tests/brd/life.feature"""
    async with sessions() as session:
        card = await create_scheduled_card(
            session, kind="action", title="Stretch", stage="today", effort_points=2,
            categories={"self"}, schedule="after completion",
        )
        again = (await finish_action(session, card.id)).successor_ids[0]
        await finish_action(session, again)
        await session.commit()
    async with sessions() as session:
        records = await life_records(session, utcnow())
    assert [(action.effort, action.categories) for action in records.actions] == [
        (2, frozenset({"self"})),
        (2, frozenset({"self"})),
    ]


# --------------------------------------------------------------------------- the pictures


def test_lf_paint_004_each_picture_colours_the_weeks_by_one_thing() -> None:
    """LF-PAINT-004 — tests/brd/life.feature"""
    assert offered(_records()) == []
    with pytest.raises(DomainError, match="Nothing is recorded yet"):
        picture(LifeChart.FEELING, _records(), GRID)
    assert offered(RECORDS) == list(LifeChart)

    # Feeling: the week's mean, red at 0 and green at 10.
    assert blend(FEELING_STOPS, 0) == FEELING_STOPS[0]
    assert blend(FEELING_STOPS, 1) == FEELING_STOPS[-1]
    feeling = picture(LifeChart.FEELING, RECORDS, GRID).paints[_week(date(2026, 8, 15))]
    assert (feeling.color, feeling.label) == (blend(FEELING_STOPS, 0.7), "7")

    # Actions: every finished Action, estimated or not.
    actions = picture(LifeChart.ACTIONS, RECORDS, GRID).paints
    assert actions[_week(date(2026, 8, 15))].label == "3"
    assert actions[_week(date(2026, 9, 19))].label == "1"

    # Effort Points: offered while they are on; a week with no estimate is pale.
    assert LifeChart.EFFORT not in offered(_records(actions=ACTIONS))
    effort = picture(LifeChart.EFFORT, RECORDS, GRID).paints
    assert effort[_week(date(2026, 8, 15))].label == "4"
    assert _week(date(2026, 9, 19)) not in effort

    # Sprints: the one with most of the week's days, its mark on its first week, and
    # neighbours alternating in depth.
    sprints = picture(LifeChart.SPRINTS, RECORDS, GRID).paints
    first, second, running = (
        sprints[_week(day)] for day in (date(2026, 8, 8), date(2026, 8, 22), date(2026, 9, 19))
    )
    assert (first.color, first.label) == (MET_COLORS[True], "✓")
    assert sprints[_week(date(2026, 8, 15))] == type(first)(MET_COLORS[True], "")
    assert (second.color, second.label) == (blend((SURFACE, MET_COLORS[False]), 0.6), "✗")
    assert (running.color, running.label) == (RUNNING_COLOR, "•")
    assert _week(SINCE - timedelta(days=1)) not in sprints


def test_lf_share_005_categories_energy_and_values_show_the_mix_or_one_share() -> None:
    """LF-SHARE-005 — tests/brd/life.feature"""
    mixed = picture(LifeChart.CATEGORY, RECORDS, GRID).paints
    # Two of three carried Work; the one carrying none is in neither share.
    august = mixed[_week(date(2026, 8, 15))]
    assert (august.color, august.mix) == (CATEGORY_COLORS["work"], ((CATEGORY_COLORS["work"], 1.0),))
    # A tie goes to the one listed first.
    first = mixed[_week(date(2026, 8, 8))]
    assert first.color == CATEGORY_COLORS["self"]
    assert first.mix == ((CATEGORY_COLORS["self"], 0.5), (CATEGORY_COLORS["work"], 0.5))

    work = picture(LifeChart.CATEGORY, RECORDS, GRID, "work").paints
    assert work[_week(date(2026, 8, 15))] == type(august)(tint(CATEGORY_COLORS["work"], 2 / 3), "67")
    assert work[_week(date(2026, 9, 19))].label == "0"

    assert focuses(LifeChart.CATEGORY, RECORDS) == ("self", "work")
    assert focuses(LifeChart.VALUE, RECORDS) == ("Health", "Craft")
    with pytest.raises(DomainError, match="No Value is called Sleep"):
        picture(LifeChart.VALUE, RECORDS, GRID, "Sleep")
    # A Value is named as the owner wrote it, whatever the capitals asked for.
    assert picture(LifeChart.VALUE, RECORDS, GRID, "health").title == "Value: Health"

    many = _records(
        actions=tuple(
            _action(SINCE, values=tuple(f"V{index}" for index in range(count)))
            for count in range(1, LIFE_VALUES_SHOWN + 2)
        ),
        values=tuple(f"V{index}" for index in range(LIFE_VALUES_SHOWN + 1)),
    )
    assert focuses(LifeChart.VALUE, many) == tuple(f"V{index}" for index in range(LIFE_VALUES_SHOWN))
    swatches = picture(LifeChart.VALUE, many, GRID).swatches
    assert len(swatches) == LIFE_VALUES_SHOWN + 1
    assert swatches[-1] == (OTHER_COLOR, "other Values")


async def test_lf_share_005_an_action_serves_the_values_of_its_goal_and_subgoal(sessions) -> None:
    """LF-SHARE-005 — tests/brd/life.feature"""
    async with sessions() as session:
        health, family, craft = [
            await create_value(session, name) for name in ("Health", "Family", "Craft")
        ]
        goal = await create_card(session, kind="goal", title="Be well", value_ids={health.id})
        subgoal = await create_card(
            session, kind="subgoal", title="Home", parent_id=goal.id, value_ids={family.id}
        )
        action = await create_card(
            session, kind="action", title="Cook", parent_id=subgoal.id, value_ids={craft.id}
        )
        alone = await create_card(session, kind="action", title="Read")
        for card in (action, alone):
            await finish_action(session, card.id)
        await session.commit()
    async with sessions() as session:
        records = await life_records(session, utcnow())
    assert [set(finished.values) for finished in records.actions] == [
        {"Health", "Family", "Craft"}, set()
    ]
    assert records.values == ("Craft", "Family", "Health")


async def test_lf_album_006_a_picture_arrives_as_an_album_of_the_whole_life_and_a_close_up(
    sessions,
) -> None:
    """LF-ALBUM-006 — tests/brd/life.feature"""
    services = services_for(sessions)
    async with sessions() as session:
        today = await local_today(session, utcnow())
        await set_birth_date(session, BORN, today)
        await create_diary_entry(session, entry_date=today, body="Fine.", feeling_score=6)
        for title, categories in (("Write", {"work"}), ("Walk", {"self"})):
            card = await create_card(session, kind="action", title=title, categories=categories)
            await finish_action(session, card.id)
        await session.commit()
    life = QueueTestMessage(message_id=960, answer_as_new=True)
    await render_retro_list(life, services)
    await _tap(life, services, "⏳ Life in weeks")

    sending = life.bot.typing_calls
    await _tap(life, services, "😊 Feeling")
    assert life.bot.typing_calls == sending + 1
    assert 960 in life.bot.deleted
    assert [photo.filename for photo in life.bot.photos_sent[-1]] == ["life.png", "close_up.png"]
    words = life.sent[-1]
    week = GRID.start(GRID.cell(today))
    week_of = f"week of {week.day} {MONTHS[week.month - 1]}"
    assert words.text == (
        f"<b>⏳ Life in weeks · Feeling</b>\nMean 6.0 over 1 week · best 6.0, {week_of} · "
        f"lowest 6.0, {week_of}"
    )
    assert words.buttons() == ["↩️ Back", "↩️ Menu"]
    album = [sent.message_id for sent in life.sent[:-1]]
    await _tap(words, services, "↩️ Back")
    assert set(album) <= set(life.bot.deleted)
    assert life.rendered[-1].startswith("<b>⏳ Life in weeks</b>")

    # A picture by what the Actions carried offers each one alone, and draws it again.
    await _tap(words, services, "🏷 Categories")
    mix = life.sent[-1]
    assert mix.text.endswith("Tap one to see its share of each week.")
    assert mix.buttons() == ["✓ All", "🌱 Self", "💰 Work", "↩️ Back", "↩️ Menu"]
    album = [sent.message_id for sent in life.sent[:-1] if sent.photo]
    await _tap(mix, services, "💰 Work")
    assert {mix.message_id, *album} <= set(life.bot.deleted)
    work = life.sent[-1]
    assert work.text.startswith("<b>⏳ Life in weeks · Category: Work</b>\nWork: 1 Action, 50% of all")
    assert work.buttons() == ["All", "🌱 Self", "✓ 💰 Work", "↩️ Back", "↩️ Menu"]

    # The best week carries the star.
    shown = picture(LifeChart.FEELING, RECORDS, GRID)
    assert shown.best == _week(date(2026, 9, 26))

    # A Value is named without what the font cannot draw.
    named = _records(actions=(_action(TODAY, values=("💪 Здоровье",)),), values=("💪 Здоровье",))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for focus in (None, "💪 Здоровье"):
            draw_life(picture(LifeChart.VALUE, named, GRID, focus), GRID, TODAY, SINCE)
    assert not [warning for warning in caught if "Glyph" in str(warning.message)]

    # The legend names each colour, a long name cut short, its keys apart and inside the
    # picture, the scale as much as the colours of nine Values.
    long = tuple(
        f"{word} и всё, что с этим связано"
        for word in ("Дом", "Деньги", "Друзья", "Здоровье", "Музыка", "Ремесло", "Семья", "Учёба", "Путь")
    )
    many = _records(
        actions=tuple(_action(TODAY - timedelta(days=day), values=(name,)) for day, name in enumerate(long)),
        values=long,
    )
    span = Span.of(GRID, TODAY, SINCE)
    for shown in (picture(LifeChart.VALUE, many, GRID), picture(LifeChart.FEELING, RECORDS, GRID)):
        for _, draw in LIFE_ALBUM:
            figure = draw(shown, span)
            renderer = FigureCanvasAgg(figure).get_renderer()
            keys = [legend.get_window_extent(renderer) for legend in figure.legends]
            keys += [axes.get_window_extent(renderer) for axes in figure.axes[1:]]
            grid = figure.axes[0].get_window_extent(renderer)
            assert all(figure.bbox.x0 <= key.x0 and key.x1 <= figure.bbox.x1 for key in keys)
            assert all(key.y0 >= figure.bbox.y0 and not key.overlaps(grid) for key in keys)
            assert not any(one.overlaps(other) for index, one in enumerate(keys) for other in keys[index + 1 :])
    whole = dict(LIFE_ALBUM)["life"](picture(LifeChart.VALUE, many, GRID), span)
    # The Values' own keys, drawn after those every picture shares.
    named = {text.get_text() for text in whole.legends[-1].get_texts()}
    assert {"Деньги и всё, что с…", "other Values"} <= named
    assert max(len(name) for name in named) == 20


async def test_lf_ask_008_a_picture_asked_for_in_words_goes_to_the_chat_before_safwas_line(
    sessions, monkeypatch
) -> None:
    """LF-ASK-008 — tests/brd/life.feature"""
    services = services_for(sessions)
    owner = QueueTestMessage(message_id=980, is_bot=False, answer_as_new=True)
    monkeypatch.setattr(life_agent, "owner_anchor", lambda bot, owner_id: owner)
    tool = life_agent.show_life_tool(
        AgentContext(
            owner_id=42,
            timezone="Europe/Istanbul",
            query_runner=None,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            sessions=sessions,
            chat=services.chat,
            bot=owner.bot,
        )
    )

    async def show(**arguments) -> dict:
        return await tool.run(
            ToolCall(id="show", name="show_life", arguments_json=json.dumps(arguments))
        )

    # No birth date: nothing is sent, and Safwa says where to set one.
    unset = await show()
    assert unset["sent"] is None
    assert "Retro → ⏳ Life in weeks → ⚙️ Settings" in unset["note"]
    assert owner.bot.photos_sent == []

    async with sessions() as session:
        await set_birth_date(session, BORN, await local_today(session, utcnow()))
        health = await create_value(session, "Health")
        card = await create_card(
            session, kind="action", title="Walk", categories={"self"}, value_ids={health.id}
        )
        await finish_action(session, card.id)
        await session.commit()

    # Asked for no picture by name: the first with records, its line naming the others.
    sending = owner.bot.typing_calls
    first = await show()
    assert owner.bot.typing_calls == sending + 1
    assert first["sent"] == "Life in weeks · Actions"
    assert first["others"] == ["category", "value"]
    assert "names the other pictures" in first["next"]
    assert [photo.filename for photo in owner.bot.photos_sent[-1]] == ["life.png", "close_up.png"]
    assert owner.rendered == [] and owner.markups == []

    # One picture, and one Category or Value alone.
    mix = await show(chart="category")
    assert mix["sent"] == "Life in weeks · Categories" and "others" not in mix
    assert (await show(category="self"))["sent"] == "Life in weeks · Category: Self"
    assert (await show(chart="value", value="health"))["sent"] == "Life in weeks · Value: Health"
    album = [sent.message_id for sent in owner.sent]

    # A choice made wrong is refused with the call to make instead, and nothing is sent.
    sent_before = len(owner.bot.photos_sent)
    crossed = await show(chart="energy", category="self")
    assert crossed["hint"] == 'Retry with {"chart": "category", "category": "self"}.'
    assert (await show(category="self", value="Health"))["code"] == "invalid_arguments"
    assert (await show(chart="pie"))["code"] == "invalid_arguments"
    assert (await show(category="leisure"))["code"] == "invalid_arguments"
    nobody = await show(value="Family")
    assert nobody["error"] == "No Value is called Family."
    assert nobody["hint"] == 'Retry with "value" one of: Health.'
    empty = await show(chart="feeling")
    assert empty["hint"] == 'Retry with "chart" one of: actions, category, value, or without it.'
    assert len(owner.bot.photos_sent) == sent_before

    # The album stays in the chat when the next screen comes.
    await dismiss_prior_ui(QueueTestMessage(message_id=990, is_bot=False, parent=owner), services)
    assert not set(album) & set(owner.bot.deleted)


def test_lf_stats_007_the_heading_says_what_the_picture_adds_up_to() -> None:
    """LF-STATS-007 — tests/brd/life.feature"""
    assert LIFE_TREND_WEEKS == 4

    def stats(chart: LifeChart, focus: str | None = None) -> tuple[str, ...]:
        return picture(chart, RECORDS, GRID, focus).stats

    assert stats(LifeChart.FEELING) == (
        "Mean 6.0 over 4 weeks · best 9.0, week of 26 Sep · lowest 3.0, week of 8 Aug",
        "Last 4 weeks 7.0, the 4 before 5.0",
    )
    assert stats(LifeChart.ACTIONS) == (
        "7 Actions finished · 0.9 a week · most 3, week of 15 Aug · "
        "longest run 2 weeks with one finished",
    )
    assert stats(LifeChart.EFFORT) == (
        "11 EP finished · 1.4 a week · most 5 EP, week of 26 Sep · "
        "1 week with finished Actions and no estimate",
    )
    assert stats(LifeChart.SPRINTS) == (
        "2 Sprints ended · criteria met in 1 · longest run met 1 · "
        "Sprint 26.09-01 running, day 14 of 14",
    )
    assert stats(LifeChart.CATEGORY) == (
        "Work leads: 4 Actions, 57% · Rest least: 0 Actions, 0%",
        "Weeks led: Self 2 · Work 2",
    )
    assert stats(LifeChart.CATEGORY, "work") == (
        "Work: 4 Actions, 57% of all · highest 100%, week of 26 Sep · absent 1 of 4 weeks",
        "Last 4 weeks 50%, the 4 before 60%",
    )
    assert stats(LifeChart.VALUE) == (
        "Health served most: 3 Actions, 43% of all finished",
        "Craft: last served 1 week ago · 1 Value never served",
    )
    assert stats(LifeChart.VALUE, "Health") == (
        "Health: 3 Actions over 3 weeks · highest 100%, week of 26 Sep",
        "Served this week · longest gap 5 weeks",
    )
    assert stats(LifeChart.VALUE, "Family") == ("Family: no finished Action carried it yet",)
