"""The retro charts: pictures of what the chosen ended Sprints added up to."""

from __future__ import annotations

import json
import re
import warnings
from datetime import date, timedelta
from io import BytesIO

from matplotlib.backends.backend_agg import FigureCanvasAgg
from PIL import Image
from telegram_fakes import QueueTestMessage
from ui_harness import FakeCallback, services_for

import safwa.features.retro.agent as retro_agent
from llm_gateway import ToolCall
from safwa.features.cards.telegram import (
    CATEGORY_COLORS,
    CATEGORY_EMOJIS,
    ENERGY_COLORS,
    ENERGY_EMOJIS,
)
from safwa.features.cards.use_cases import create_card, finish_action
from safwa.features.planning.closing import (
    CATEGORY_BUCKETS,
    ENERGY_BUCKETS,
    NONE_BUCKET,
    Bucket,
    DayTally,
    RetroStatistics,
    SeriesTally,
    TimedAction,
)
from safwa.features.planning.model import Sprint
from safwa.features.planning.use_cases import finish_sprint, start_sprint
from safwa.features.retro.charts import (
    CHART_SIZE,
    CHART_SPRINTS_READABLE,
    CHARTS,
    CHECKS_SHOWN,
    LOWER_BOUNDS,
    MISSED_EMOJI,
    NONE_COLOR,
    PASSED_EMOJI,
    ChartSprint,
    ChosenSprints,
    check_totals,
    counts_effort,
    emoji_file,
    render_charts,
)
from safwa.features.retro.telegram import open_retro, render_retro_list
from safwa.foundation.charts import CHART_DPI, drawable
from telegram_llm import TELEGRAM_ALBUM_LIMIT
from tg_agent_shell.telegram import callback_token_handler, dismiss_prior_ui
from tg_agent_shell.telegram.manifest import AgentContext


async def _ended_with_work(sessions, count: int) -> list[Sprint]:
    """`count` Sprints, the oldest first, each finishing one Work Action of cognitive energy."""
    ended = []
    async with sessions() as session:
        for index in range(count):
            card = await create_card(
                session,
                kind="action",
                title=f"Ship {index}",
                stage="sprint",
                effort_points=2,
                categories={"work"},
                energy_types={"cognitive"},
            )
            sprint = await start_sprint(session, success_criteria=f"Goal {index}")
            await finish_action(session, card.id)
            await finish_sprint(session)
            ended.append(sprint)
        await session.commit()
    return ended


async def _tap(message: QueueTestMessage, services, text: str) -> None:
    """Press the button with this text on the screen the message holds now."""
    [button] = [
        button
        for row in message.markups[-1].inline_keyboard
        for button in row
        if button.text == text
    ]
    await callback_token_handler(
        FakeCallback(button.callback_data.split(":", 1)[1], message), services
    )


def _album(message: QueueTestMessage) -> list[str]:
    return [photo.filename.removesuffix(".png") for photo in message.bot.photos_sent[-1]]


async def test_rt_chart_018_the_charts_of_the_sprints_that_ended_arrive_as_one_album(
    sessions,
) -> None:
    """RT-CHART-018 — tests/brd/retro.feature"""
    older, newer = await _ended_with_work(sessions, 2)
    services = services_for(sessions)

    # From the list: every Sprint that ended, in place of the list.
    listed = QueueTestMessage(message_id=900, answer_as_new=True)
    await render_retro_list(listed, services)
    bot = listed.bot
    sending = bot.typing_calls
    await _tap(listed, services, "📈 Charts of recent Sprints")

    assert bot.typing_calls == sending + 1
    assert 900 in bot.deleted
    assert _album(listed) == [
        "burnup", "velocity", "category", "mix", "energy", "category_energy", "week"
    ]
    words = listed.sent[-1]
    assert words.text.startswith(
        f"<b>📈 Charts</b>\n2 Sprints, {older.number} to {newer.number} · "
    )
    assert words.buttons() == ["↩️ Back", "↩️ Menu"]
    album = [sent.message_id for sent in listed.sent[:-1]]
    await _tap(words, services, "↩️ Back")
    assert "<b>Retro</b>" in listed.rendered[-1]
    assert set(album) <= set(bot.deleted)

    # From one retro: that Sprint's charts, and the way back leads to its retro.
    retro = QueueTestMessage(message_id=950, answer_as_new=True)
    await open_retro(retro, services, newer.id)
    await _tap(retro, services, "📈 Charts")
    assert _album(retro) == ["burnup", "category", "energy", "category_energy", "week"]
    words = retro.sent[-1]
    assert words.text.startswith(f"<b>📈 Charts</b>\nSprint {newer.number} · ")
    album = [sent.message_id for sent in retro.sent[:-1]]
    await _tap(words, services, "↩️ Back")
    assert f"Sprint {newer.number} retro" in retro.rendered[-1]
    assert set(album) <= set(retro.bot.deleted)

    # More Sprints than the list's charts hold: the newest, and the words say of how many.
    newest = await _ended_with_work(sessions, CHART_SPRINTS_READABLE - 1)
    many = QueueTestMessage(message_id=980, answer_as_new=True)
    await render_retro_list(many, services)
    await _tap(many, services, "📈 Charts of recent Sprints")
    words = many.sent[-1].text
    assert f"\n{CHART_SPRINTS_READABLE} Sprints, {newer.number} to {newest[-1].number} · " in words
    assert words.endswith(
        f"The newest {CHART_SPRINTS_READABLE} of the {CHART_SPRINTS_READABLE + 1} Sprints that "
        "ended. Ask Safwa in words for others."
    )


def _statistics(
    first: date,
    *,
    time_tracking: bool = False,
    series: tuple[SeriesTally, ...] = (),
    unestimated: int = 0,
    unknown_schedules: int = 0,
    together: bool = True,
) -> RetroStatistics:
    """A three-day Sprint that took two Work Actions of cognitive energy and finished one."""
    timed = 90 if time_tracking else 0
    bucket = Bucket(
        effort=4, done_effort=2, count=2, done_count=1, minutes=timed,
        timed_count=1 if timed else 0, timed_effort=2 if timed else 0,
    )
    return RetroStatistics(
        taken=4, done=2, initial=3, added=1, removed=0, planned=2, finished=1, remaining=1,
        blocked=0, key_total=0, key_finished=0, key_unknown=0,
        by_category={"work": bucket}, by_energy={"cognitive": bucket},
        days=tuple(
            DayTally(day=(first + timedelta(days=index)).isoformat(), planned=1, done=done)
            for index, done in enumerate((1, 0, 0))
        ),
        series=series, time_tracking=time_tracking, active_day_minutes=600 if timed else 0,
        minutes=timed, timed=1 if timed else 0, timed_effort=2 if timed else 0,
        longest=(TimedAction("Квартальный отчёт", timed),) if timed else (),
        unestimated=unestimated,
        unknown_schedules=unknown_schedules,
        by_category_energy={"work": {"cognitive": bucket}} if together else {},
    )


def _sprint(index: int, **record) -> ChartSprint:
    statistics = _statistics(date(2026, 9, 1) + timedelta(days=3 * index), **record)
    return ChartSprint(f"26.09-0{index + 1}", statistics, met=index % 2 == 0, capacity=5)


def _words(name: str, sprints: list[ChartSprint], pattern: str) -> tuple[list, object]:
    """The boxes the words matching `pattern` take on one chart as drawn, and its axes' box."""
    figure = dict(CHARTS)[name](ChosenSprints(tuple(sprints), effort_tracking=False))
    renderer = FigureCanvasAgg(figure).get_renderer()
    axes = figure.axes[0]
    boxes = [
        (text.get_text(), text.get_window_extent(renderer))
        for text in axes.texts
        if re.fullmatch(pattern, text.get_text())
    ]
    return boxes, axes.get_window_extent(renderer)


def _said(name: str, sprints: list[ChartSprint], *, effort_tracking: bool = False) -> list[str]:
    """Every word one chart writes, on the picture and on its axes."""
    figure = dict(CHARTS)[name](ChosenSprints(tuple(sprints), effort_tracking))
    return [text.get_text() for artist in (figure, *figure.axes) for text in artist.texts]


def test_rt_chart_019_the_album_holds_the_charts_its_sprints_carry() -> None:
    """RT-CHART-019 — tests/brd/retro.feature"""
    def names(sprints: list[ChartSprint], *, effort_tracking: bool = False) -> list[str]:
        return [name for name, _ in render_charts(sprints, effort_tracking=effort_tracking)]

    # One Sprint, Effort Points off, no time, no Check, and nothing kept of the two together.
    assert names([_sprint(0, together=False)]) == ["burnup", "category", "energy", "week"]
    assert names([_sprint(0)]) == ["burnup", "category", "energy", "category_energy", "week"]
    # Two or more Sprints compare; Effort Points add the plan and the capacity; a Sprint
    # tracked and a Check answered add theirs.
    habit = SeriesTally("Ran before work?", ("Health",), 2, 1)
    every = [_sprint(0, time_tracking=True), _sprint(1, series=(habit,)), _sprint(2)]
    assert names(every, effort_tracking=True) == [name for name, _ in CHARTS]
    assert len(CHARTS) <= TELEGRAM_ALBUM_LIMIT
    assert "capacity" not in names(every)
    # Not one Sprint estimated in full: no plan and capacity to draw.
    rough = [_sprint(0, unestimated=1), _sprint(1, unestimated=2)]
    assert "capacity" not in names(rough, effort_tracking=True)

    # Effort Points are counted when they are on and every Sprint was estimated in full.
    assert counts_effort(every, effort_tracking=True)
    assert not counts_effort(every, effort_tracking=False)
    assert not counts_effort([*every, _sprint(3, unestimated=1)], effort_tracking=True)

    # A Sprint that did not know its Schedule quantities took at least so much: no share
    # finished, no Effort Points, and no plan and capacity of its own.
    unknown = [_sprint(0, unknown_schedules=1), _sprint(1)]
    assert not counts_effort(unknown, effort_tracking=True)
    for name in ("burnup", "velocity", "category", "energy"):
        assert any(LOWER_BOUNDS in text for text in _said(name, unknown)), name
        assert not any(LOWER_BOUNDS in text for text in _said(name, [_sprint(0), _sprint(1)])), name
    assert {"1/≥2", "1/2"} <= set(_said("burnup", unknown))
    assert "2 of at least 4" in _said("category", unknown)
    assert "2 of 4 · 50%" in _said("category", [_sprint(0), _sprint(1)])
    assert not any("%" in text for text in _said("energy", unknown))
    capacity = _said("capacity", [*unknown, _sprint(2)], effort_tracking=True)
    assert "1 of 3 Sprints are left out: an estimate or a Schedule quantity was unknown." in capacity

    # Checks are added up over the Sprints, the most answered first.
    series = tuple(
        SeriesTally(f"Check {index}", ("Health",), index, 1) for index in range(CHECKS_SHOWN + 2)
    )
    totals = check_totals([_sprint(0, series=series), _sprint(1, series=series[:1])])
    assert totals[0] == (("Check 9", ("Health",)), (9, 1))
    assert dict(totals)[("Check 0", ("Health",))] == (0, 2)
    answered = [passed + missed for _, (passed, missed) in totals]
    assert answered == sorted(answered, reverse=True)
    assert "checks" in names([_sprint(0, series=series)])

    # Each Sprint's numbers are written up to CHART_SPRINTS_READABLE Sprints, side by side.
    readable = [_sprint(index) for index in range(CHART_SPRINTS_READABLE)]
    for name, pattern in (("burnup", r"\d+/\d+"), ("velocity", r"\d+"), ("mix", r"\d+%")):
        boxes, _ = _words(name, readable, pattern)
        assert len(boxes) == CHART_SPRINTS_READABLE, name
        assert not any(
            one.overlaps(other) for index, (_, one) in enumerate(boxes) for _, other in boxes[index + 1 :]
        ), name
        assert _words(name, [*readable, _sprint(CHART_SPRINTS_READABLE)], pattern)[0] == [], name

    # A Check's title over its Values, cut to fit left of its bars, and drawn without emoji.
    assert drawable("💪 Здоровье 家族") == "Здоровье"
    long = SeriesTally("Пробежка до работы каждое утро без пропусков 🏃", ("💪 Здоровье",), 9, 7)
    boxes, axes = _words("checks", [_sprint(0, series=(long,))], r"(Пробежка|Здоровье).*")
    assert [text for text, _ in boxes] == ["Пробежка до работы каждое утр…", "Здоровье"]
    assert all(box.x1 < axes.x0 for _, box in boxes)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        render_charts([_sprint(0, series=(long,))], effort_tracking=False)
    assert not [warning for warning in caught if "Glyph" in str(warning.message)]

    # Each picture is what Telegram shows a photo as.
    for _, png in render_charts(every, effort_tracking=True):
        with Image.open(BytesIO(png)) as picture:
            assert picture.size == (CHART_SIZE[0] * CHART_DPI, CHART_SIZE[1] * CHART_DPI)


def test_rt_chart_020_a_category_and_an_energy_type_look_the_same_on_every_chart() -> None:
    """RT-CHART-020 — tests/brd/retro.feature"""
    assert set(CATEGORY_COLORS) | {NONE_BUCKET} == set(CATEGORY_BUCKETS)
    assert set(ENERGY_COLORS) | {NONE_BUCKET} == set(ENERGY_BUCKETS)
    assert set(CATEGORY_EMOJIS) == set(CATEGORY_COLORS)
    assert set(ENERGY_EMOJIS) == set(ENERGY_COLORS)
    named = [*CATEGORY_COLORS.values(), *ENERGY_COLORS.values()]
    assert len(set(named)) == len(named)
    assert NONE_COLOR not in named
    for emoji in (*CATEGORY_EMOJIS.values(), *ENERGY_EMOJIS.values(), PASSED_EMOJI, MISSED_EMOJI):
        assert emoji_file(emoji).is_file(), emoji


async def test_rt_chart_021_charts_asked_for_in_words_go_to_the_chat_before_safwas_line(
    sessions, monkeypatch
) -> None:
    """RT-CHART-021 — tests/brd/retro.feature"""
    older, newer = await _ended_with_work(sessions, 2)
    services = services_for(sessions)
    owner = QueueTestMessage(message_id=600, is_bot=False, answer_as_new=True)
    monkeypatch.setattr(retro_agent, "owner_anchor", lambda bot, owner_id: owner)

    def context(**chat) -> AgentContext:
        return AgentContext(
            owner_id=42,
            timezone="Europe/Istanbul",
            query_runner=None,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            sessions=sessions,
            **chat,
        )

    # An application that hands its read tools no chat is not given the tool at all.
    assert "show_charts" not in {tool.name for tool in retro_agent.RETRO_AGENT.read_tools(context())}
    tools = {
        tool.name: tool
        for tool in retro_agent.RETRO_AGENT.read_tools(context(chat=services.chat, bot=owner.bot))
    }

    async def show(**arguments) -> dict:
        return await tools["show_charts"].run(
            ToolCall(id="show", name="show_charts", arguments_json=json.dumps(arguments))
        )

    # Every Sprint that ended: the charts go to the chat, with no words or buttons of their own.
    every = ["burnup", "velocity", "category", "mix", "energy", "category_energy", "week"]
    sending = owner.bot.typing_calls
    result = await show()
    assert owner.bot.typing_calls == sending + 1
    assert result["charts"] == every
    assert result["sent"].startswith(f"2 Sprints, {older.number} to {newer.number} · ")
    assert "one short line" in result["next"]
    assert [photo.filename for photo in owner.bot.photos_sent[-1]] == [f"{name}.png" for name in every]
    assert owner.rendered == [] and owner.markups == []

    # One chart of one Sprint.
    result = await show(numbers=[newer.number], chart="energy")
    assert result["charts"] == ["energy"]
    assert [photo.filename for photo in owner.bot.photos_sent[-1]] == ["energy.png"]
    album = [sent.message_id for sent in owner.sent]

    # A choice made wrong, a number with no Sprint that ended, and a chart the Sprints do not
    # carry are refused, and nothing is sent.
    sent_before = len(owner.bot.photos_sent)
    mixed = await show(numbers=[newer.number], start_date="2026-09-01", end_date="2026-09-30")
    assert mixed["code"] == "invalid_arguments"
    unknown = await show(numbers=["99.99-99"])
    assert unknown["error"] == "No Sprint has the number 99.99-99."
    assert unknown["hint"] == "Retry with the numbers of Sprints that ended."
    one = await show(numbers=[newer.number], chart="velocity")
    assert one["error"] == "These Sprints have no velocity chart."
    assert one["hint"] == (
        'Retry with "chart" one of: burnup, category, energy, category_energy, week, '
        "or without it."
    )
    assert (await show(chart="pie"))["code"] == "invalid_arguments"
    assert len(owner.bot.photos_sent) == sent_before

    # They stay in the chat when the next screen comes.
    await dismiss_prior_ui(QueueTestMessage(message_id=700, is_bot=False, parent=owner), services)
    assert not set(album) & set(owner.bot.deleted)
