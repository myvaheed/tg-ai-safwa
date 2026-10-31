from __future__ import annotations

import html
import json
from datetime import date, timedelta

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn, route_turn, schedule_turn
from review_e2e_helpers import QueueTestCallback, QueueTestMessage, review_services
from schedule_helpers import create_card, create_check
from sqlalchemy import select
from test_diary_e2e import TODAY as DIARY_TODAY
from test_diary_e2e import turn as diary_turn
from test_reminder_e2e import condition_turns, reminder_script
from test_reminder_e2e import turn as reminder_turn
from test_subagent_e2e import diary_subagent
from ui_harness import kind_of

from safwa.bootstrap.modules import PROPOSALS
from safwa.constants import DESCRIPTION_MAX_CHARS
from safwa.features.cards.api import EFFORT_POINTS_ABOUT
from safwa.features.cards.model import EFFORT_RUNGS, Card, effort_label
from safwa.features.cards.telegram import render_card_choices
from safwa.features.cards.telegram.effort_page import EFFORT_POINTS_TEXT
from safwa.features.cards.telegram.selectors import CHOICE_TITLES
from safwa.features.cards.use_cases import delete_card, finish_action, toggle_card_value
from safwa.features.checks.telegram.handlers import NOTE_INSTRUCTION as CHECK_NOTE_INSTRUCTION
from safwa.features.checks.use_cases import toggle_check_value
from safwa.features.diary.telegram import FEELING_SCORE_EMOJI as FEELING
from safwa.features.diary.use_cases import create_diary_entry
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
from safwa.features.reminders.telegram.screens import TEXT_INSTRUCTION as REMINDER_TEXT_INSTRUCTION
from safwa.features.schedules.api import remind_label
from safwa.features.schedules.telegram import REMIND_INSTRUCTION, REMIND_LEADS
from safwa.features.tags.use_cases import create_tag
from safwa.features.values.use_cases import create_value
from tg_agent_shell.ai.contracts import AgentChange
from tg_agent_shell.foundation.clock import utcnow
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.proposals.model import ChangeAction, ProposalChange
from tg_agent_shell.proposals.render import USER_EDITED, USER_EDITED_NEXT
from tg_agent_shell.proposals.store import PROPOSAL_REVIEW_MINUTES
from tg_agent_shell.proposals.telegram import render_proposal
from tg_agent_shell.telegram import callback_token_handler, dismiss_prior_ui, expire_review
from tg_agent_shell.telegram.dialogue import ordinary_text
from tg_agent_shell.telegram.model import UiSession
from tg_agent_shell.telegram.text_input import handle_text_input

pytestmark = pytest.mark.e2e

DECISION = ["✏️ Edit", "✅ Update", "🗑 Discard"]


def rung(points: float) -> str:
    return f"{effort_label(points)} · {EFFORT_RUNGS[points]}"


async def press(services, message: QueueTestMessage, text: str) -> None:
    """Press the button reading `text` on the screen drawn last."""
    button = next(
        button
        for row in message.markups[-1].inline_keyboard
        for button in row
        if button.text == text
    )
    token = button.callback_data.split(":", 1)[1]
    await callback_token_handler(QueueTestCallback(token, message), services)


def drawn(message: QueueTestMessage) -> str:
    """What the chat shows last, whether a message or the Bot API drew it."""
    return message.bot.drawn[-1]


def redrawn_buttons(message: QueueTestMessage) -> list[str]:
    """The buttons of the screen the Bot API redrew last, as it does after typed words."""
    markup = message.bot.edited[-1][1]
    return [button.text for row in markup.inline_keyboard for button in row]


async def type_words(services, message: QueueTestMessage, words: str) -> QueueTestMessage:
    """The owner sending `words` while the screen drawn last waits."""
    typed = QueueTestMessage(
        message_id=message.message_id + 50, text=words, is_bot=False, parent=message
    )
    await ordinary_text(typed, services)
    return typed


async def review(
    e2e_harness, calls, *answers: str, request: str = "Change it", agent: str = "workspace_mutator"
):
    """Safwa's proposal from `calls` by `agent`, drawn as its review screen."""
    advisor, provider = e2e_harness.advisor([route_turn(agent), mutation_turn(*calls), *answers])
    outcome = await advisor.handle(request)
    assert outcome.proposal_id is not None
    services = review_services(e2e_harness, advisor)
    message = QueueTestMessage()
    await render_proposal(message, services, outcome.proposal_id)
    return advisor, provider, services, message, outcome.proposal_id


async def run_action(e2e_harness, **fields) -> int:
    async with e2e_harness.sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        card = await create_manual_card(session, title="Run", effort_points=None, **fields)
        await session.commit()
        return card.id


async def effort_review(e2e_harness, *answers: str):
    card_id = await run_action(e2e_harness)
    advisor, provider, services, message, proposal_id = await review(
        e2e_harness,
        [("action", {"mode": "update", "id": card_id, "effort_points": 5})],
        *answers,
        request="Make Run 5 EP",
    )
    return card_id, advisor, provider, services, message, proposal_id


async def values(e2e_harness, *names: str) -> dict[str, int]:
    async with e2e_harness.sessions() as session:
        created = {name: await create_value(session, name, f"{name} matters.") for name in names}
        await session.commit()
        return {name: value.id for name, value in created.items()}


def tool_results(provider, call: int) -> list[dict]:
    return [
        json.loads(str(message["content"]))
        for message in provider.calls[call]
        if message["role"] == "tool"
    ]


async def test_edit_offers_the_effort_alone_with_the_actions_own_choices(e2e_harness):
    """PR-EDIT-033 — tests/brd/tg_agent_shell/proposals.feature"""
    card_id, _advisor, _provider, services, message, _proposal_id = await effort_review(
        e2e_harness
    )
    assert message.buttons() == DECISION

    await press(services, message, "✏️ Edit")
    assert "✏️ Update Action" in message.rendered[-1]
    assert message.buttons() == ["🔢 Effort", "↩️ Back"]
    assert await kind_of(e2e_harness.sessions, message) == MessageKind.APPROVAL.value

    await press(services, message, "🔢 Effort")
    assert message.rendered[-1] == f"<b>{CHOICE_TITLES['effort']}</b>"
    offered = message.buttons()
    assert f"✓ {rung(5)}" in offered and offered[-1] == "↩️ Back"
    own = QueueTestMessage(message_id=901)
    await render_card_choices(own, services, "card_choose_effort", card_id)
    untick = [label.removeprefix("✓ ") for label in offered]
    assert untick == [label.removeprefix("✓ ") for label in own.buttons()]
    assert await kind_of(e2e_harness.sessions, message) == MessageKind.APPROVAL.value


async def test_edit_offers_the_values_a_link_or_an_unlink_names(e2e_harness):
    """PR-EDIT-033 — tests/brd/tg_agent_shell/proposals.feature"""
    ids = await values(e2e_harness, "Calm", "Health", "Focus")
    card_id = await run_action(e2e_harness)
    async with e2e_harness.sessions() as session:
        await toggle_card_value(session, card_id, ids["Calm"])
        await session.commit()

    _advisor, _provider, services, message, _id = await review(
        e2e_harness, [("action", {"mode": "link", "id": card_id, "values": ["Health"]})]
    )
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["💎 Values", "↩️ Back"]
    await press(services, message, "💎 Values")
    assert message.rendered[-1] == "<b>Link Values</b>"
    assert message.buttons() == ["✓ Health", "Focus", "↩️ Back"]

    _advisor, _provider, services, message, _id = await review(
        e2e_harness, [("action", {"mode": "unlink", "id": card_id, "values": ["Calm"]})]
    )
    await press(services, message, "✏️ Edit")
    await press(services, message, "💎 Values")
    assert message.rendered[-1] == "<b>Unlink Values</b>"
    assert message.buttons() == ["✓ Calm", "↩️ Back"]


async def test_edit_is_offered_only_when_a_field_can_be_set_by_hand(e2e_harness):
    """PR-EDIT-033 — tests/brd/tg_agent_shell/proposals.feature"""
    ids = await values(e2e_harness, "Calm", "Health")
    card_id = await run_action(e2e_harness)
    async with e2e_harness.sessions() as session:
        await toggle_card_value(session, card_id, ids["Calm"])
        await session.commit()

    async with e2e_harness.sessions() as session:
        tag = await create_tag(session, "Sport")
        await session.commit()
    _advisor, _provider, _services, message, _id = await review(
        e2e_harness, [("remove", {"mode": "delete", "entity": "tag", "id": tag.id})]
    )
    assert message.buttons() == ["✅ Delete", "🗑 Discard"]

    # Two changes of one proposal both set the Values, so no one button could say which.
    _advisor, _provider, _services, message, _id = await review(
        e2e_harness,
        [
            ("action", {"mode": "link", "id": card_id, "values": ["Health"]}),
            ("action", {"mode": "unlink", "id": card_id, "values": ["Calm"]}),
        ],
    )
    assert message.buttons() == ["✅ Update", "🗑 Discard"]


async def test_a_picked_value_is_checked_and_marked_and_nothing_is_written(e2e_harness):
    """PR-EDIT-034 — tests/brd/tg_agent_shell/proposals.feature"""
    card_id, advisor, _provider, services, message, proposal_id = await effort_review(
        e2e_harness
    )
    await press(services, message, "✏️ Edit")
    await press(services, message, "🔢 Effort")
    await press(services, message, rung(3))

    screen = message.rendered[-1]
    assert "✏️ Update Action" in screen
    assert "<i>✍️ Edited by you:</i> 🔢 Effort" in screen
    assert message.buttons() == DECISION
    assert advisor.reviews.proposal(proposal_id).changes[0].values["effort_points"] == 3
    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card_id)).effort_points is None

    # One choice, or Edit itself, goes back without changing anything.
    await press(services, message, "✏️ Edit")
    await press(services, message, "🔢 Effort")
    await press(services, message, "↩️ Back")
    assert message.buttons() == ["🔢 Effort", "↩️ Back"]
    await press(services, message, "↩️ Back")
    assert message.buttons() == DECISION
    assert advisor.reviews.proposal(proposal_id).changes[0].values["effort_points"] == 3


async def test_a_refused_value_leaves_the_proposal_as_it_was(e2e_harness):
    """PR-EDIT-034 — tests/brd/tg_agent_shell/proposals.feature"""
    card_id = await run_action(e2e_harness)
    advisor, _provider, services, message, proposal_id = await review(
        e2e_harness, [("action", {"mode": "update", "id": card_id, "title": "Run far"})]
    )
    await press(services, message, "✏️ Edit")
    await press(services, message, "✏️ Title")
    # The workspace as it is now is what the value is checked against.
    async with e2e_harness.sessions() as session:
        await delete_card(session, card_id)
        await session.commit()

    await type_words(services, message, "Run farther")

    assert f"Card #{card_id} does not exist." in drawn(message)
    assert advisor.reviews.proposal(proposal_id).changes[0].values["title"] == "Run far"


async def test_ticks_move_until_back_carries_them_into_the_proposal(e2e_harness):
    """PR-EDIT-034 — tests/brd/tg_agent_shell/proposals.feature"""
    card_id = await run_action(e2e_harness)
    advisor, _provider, services, message, proposal_id = await review(
        e2e_harness,
        [("action", {"mode": "update", "id": card_id, "categories": ["growth"]})],
    )
    await press(services, message, "✏️ Edit")
    await press(services, message, "🏷 Categories")
    work = next(label for label in message.buttons() if "Work" in label)
    await press(services, message, work)

    assert f"✓ {work}" in message.buttons()
    assert advisor.reviews.proposal(proposal_id).changes[0].values["categories"] == ["growth"]

    await press(services, message, "↩️ Back")
    assert message.buttons() == DECISION
    chosen = advisor.reviews.proposal(proposal_id).changes[0].values["categories"]
    assert sorted(chosen) == ["growth", "work"]
    assert "<i>✍️ Edited by you:</i> 🏷 Categories" in message.rendered[-1]


async def test_typed_words_are_the_fields_value_and_the_review_stays_open(e2e_harness):
    """PR-EDIT-035 — tests/brd/tg_agent_shell/proposals.feature"""
    card_id = await run_action(e2e_harness)
    advisor, _provider, services, message, proposal_id = await review(
        e2e_harness, [("action", {"mode": "update", "id": card_id, "title": "Run far"})]
    )
    shown = advisor.reviews.proposal(proposal_id).shown_at
    await press(services, message, "✏️ Edit")
    # Each drawing of the review starts its time again (PROPOSAL_REVIEW_MINUTES).
    assert advisor.reviews.proposal(proposal_id).shown_at > shown
    await press(services, message, "✏️ Title")
    assert "Edit Card Title" in drawn(message)
    assert await kind_of(e2e_harness.sessions, message) == MessageKind.APPROVAL.value

    typed = await type_words(services, message, "Run farther")

    assert typed.message_id in message.bot.deleted or typed.was_deleted
    assert redrawn_buttons(message) == DECISION
    assert advisor.reviews.proposal(proposal_id).changes[0].values["title"] == "Run farther"
    assert await kind_of(e2e_harness.sessions, message) == MessageKind.APPROVAL.value


async def test_words_over_a_fields_choices_interrupt_the_review(e2e_harness):
    """PR-EDIT-035 — tests/brd/tg_agent_shell/proposals.feature"""
    _card_id, advisor, _provider, services, message, proposal_id = await effort_review(
        e2e_harness
    )
    await press(services, message, "✏️ Edit")
    await press(services, message, "🔢 Effort")

    words = QueueTestMessage(message_id=1_200, text="Actually no", is_bot=False, parent=message)
    await dismiss_prior_ui(words, services)

    assert advisor.reviews.proposal(proposal_id) is None
    assert await kind_of(e2e_harness.sessions, message) == MessageKind.EVENT.value


async def test_words_for_a_field_of_a_closed_review_are_the_owners_own(e2e_harness):
    """PR-EDIT-035 — tests/brd/tg_agent_shell/proposals.feature"""
    card_id = await run_action(e2e_harness)
    advisor, _provider, services, message, proposal_id = await review(
        e2e_harness, [("action", {"mode": "update", "id": card_id, "title": "Run far"})]
    )
    await press(services, message, "✏️ Edit")
    await press(services, message, "✏️ Title")
    proposal = advisor.reviews.proposal(proposal_id)
    proposal.shown_at = utcnow() - timedelta(minutes=PROPOSAL_REVIEW_MINUTES + 1)
    assert advisor.reviews.expired(utcnow()) is proposal

    await expire_review(message, services, proposal_id)

    assert advisor.reviews.proposal(proposal_id) is None
    async with e2e_harness.sessions() as session:
        editor = await session.scalar(select(UiSession))
    typed = QueueTestMessage(message_id=1_300, text="Run farther", is_bot=False, parent=message)
    assert await handle_text_input(typed, services, dict(editor.state)) is False


async def test_safwa_is_told_what_the_owner_changed_before_saving(e2e_harness):
    """PR-EDIT-036 — tests/brd/tg_agent_shell/proposals.feature"""
    card_id, _advisor, provider, services, message, _id = await effort_review(
        e2e_harness, "Saved with 3 EP.", "Run is 3 EP now."
    )
    await press(services, message, "✏️ Edit")
    await press(services, message, "🔢 Effort")
    await press(services, message, rung(3))
    await press(services, message, "✅ Update")

    async with e2e_harness.sessions() as session:
        assert (await session.get(Card, card_id)).effort_points == 3
    [saved] = tool_results(provider, 2)
    assert saved["status"] == "approved"
    assert saved[USER_EDITED] == {"effort_points": {"proposed": 5, "saved": 3}}
    assert saved["next"].startswith(USER_EDITED_NEXT)
    assert any("3" in line and "5" not in line for line in saved["fields"])
    [receipt] = [
        json.loads(str(message["content"]))
        for message in provider.calls[3]
        if message["role"] == "tool" and message.get("name") == "route"
    ]
    assert [line for line in receipt["did"] if "3" in line and "5" not in line]
    assert "Run is 3 EP now." in message.rendered[-1]


async def test_a_value_set_back_or_a_discard_reports_no_change(e2e_harness):
    """PR-EDIT-036 — tests/brd/tg_agent_shell/proposals.feature"""
    _card_id, _advisor, provider, services, message, _id = await effort_review(
        e2e_harness, "Saved.", "Saved."
    )
    for points in (3, 5):
        await press(services, message, "✏️ Edit")
        await press(services, message, "🔢 Effort")
        await press(services, message, rung(points))
    assert "Edited by you" not in message.rendered[-1]
    await press(services, message, "✅ Update")
    [saved] = tool_results(provider, 2)
    assert USER_EDITED not in saved

    _card_id, _advisor, provider, services, message, _id = await effort_review(
        e2e_harness, "Not saved.", "Not saved."
    )
    await press(services, message, "✏️ Edit")
    await press(services, message, "🔢 Effort")
    await press(services, message, rung(3))
    await press(services, message, "🗑 Discard")
    [discarded] = tool_results(provider, 2)
    assert discarded["status"] == "discarded"
    assert USER_EDITED not in discarded


def test_a_card_proposal_offers_the_cards_own_fields_in_its_order():
    """CD-EDIT-067 — tests/brd/cards.feature"""
    every = {
        "title": "Run",
        "note": "Far",
        "stage": "sprint",
        "priority": "low",
        "schedule": "every Monday",
        "remind": 60,
        "blocked_description": "Rain",
        "effort_points": 3,
        "tracked_mins": 30,
        "categories": ["growth"],
        "energy_types": ["physical"],
        "values": ["Health"],
        "tags": ["Sport"],
        "checks": ["Ran?"],
        "parent": "Be fit",
    }

    def labels(kind: str, *, markdown: bool = False) -> list[str]:
        call = AgentChange(
            entity="card", action="update", id=1, values={**every, "kind": kind}
        )
        # Each value survived preparation, so each is what Save writes.
        change = ProposalChange("card", ChangeAction.UPDATE, 1, values=call.values, source=call)
        fields = PROPOSALS.edit_fields([change])
        return [field.label for _i, _k, field in fields if field.markdown or not markdown]

    assert labels("action") == [
        "✏️ Title",
        "📝 Note",
        "📍 Stage",
        "⚠️ Priority",
        "⏱ Schedule",
        "🔔 Remind",
        "📝 Blocked reason",
        "🔢 Effort",
        "⌛ Time spent",
        "🏷 Categories",
        "⚡ Energy",
        "💎 Values",
        "🏷 Tags",
    ]
    assert labels("goal") == [
        "✏️ Title",
        "📝 Note",
        "⚠️ Priority",
        "⏰ Deadline",
        "🔔 Remind",
        "💎 Values",
        "🏷 Tags",
    ]
    assert labels("action", markdown=True) == ["📝 Note", "📝 Blocked reason"]


async def test_a_card_field_opens_the_cards_own_choices(e2e_harness):
    """CD-EDIT-067 — tests/brd/cards.feature"""
    async with e2e_harness.sessions() as session:
        for name in ("Sport", "Family", "Work"):
            await create_tag(session, name)
        await session.commit()
    card_id = await run_action(e2e_harness)
    _advisor, _provider, services, message, _id = await review(
        e2e_harness,
        [("action", {"mode": "update", "id": card_id, "priority": "low", "tags": ["Sport"]})],
    )
    for button, choice in (("⚠️ Priority", "priority"), ("🏷 Tags", "tags")):
        await press(services, message, "✏️ Edit")
        await press(services, message, button)
        own = QueueTestMessage(message_id=902)
        await render_card_choices(own, services, f"card_choose_{choice}", card_id)
        assert message.rendered[-1] == own.rendered[-1]
        assert [label.removeprefix("✓ ") for label in message.buttons()] == [
            label.removeprefix("✓ ") for label in own.buttons()
        ]
        await press(services, message, "↩️ Back")
        if choice == "priority":
            await press(services, message, "↩️ Back")


async def test_a_typed_schedule_is_read_before_the_review_comes_back(e2e_harness):
    """CD-EDIT-067 — tests/brd/cards.feature"""
    card_id = await run_action(e2e_harness)
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(
                ("action", {"mode": "update", "id": card_id, "schedule": "after completion"})
            ),
            schedule_turn(after_completion=True),
            schedule_turn(after_completion=True),
        ]
    )
    outcome = await advisor.handle("Repeat Run after I finish it")
    services = review_services(e2e_harness, advisor)
    message = QueueTestMessage()
    await render_proposal(message, services, outcome.proposal_id)
    await press(services, message, "✏️ Edit")
    await press(services, message, "⏱ Schedule")

    await type_words(services, message, "once I am done with it")

    assert len(provider.calls) == 4
    assert "once I am done with it" in json.dumps(provider.calls[3], ensure_ascii=False)
    values = advisor.reviews.proposal(outcome.proposal_id).changes[0].values
    assert values["schedule"] == "once I am done with it"
    assert values["schedule_rule"] == {"kind": "after_completion"}
    assert redrawn_buttons(message) == DECISION


async def named_review(e2e_harness, tool: str, item_id: int):
    return await review(
        e2e_harness,
        [(tool, {"mode": "update", "id": item_id, "name": "New", "description": "Kept."})],
    )


async def test_a_tag_proposal_is_edited_with_the_tags_own_controls(e2e_harness):
    """TA-EDIT-013 — tests/brd/tags.feature"""
    async with e2e_harness.sessions() as session:
        tag = await create_tag(session, "Old")
        await session.commit()
    advisor, _provider, services, message, proposal_id = await named_review(
        e2e_harness, "tag", tag.id
    )
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["✏️ Name", "📝 Description", "↩️ Back"]
    await press(services, message, "📝 Description")
    assert "Edit Tag Description" in drawn(message)
    assert "Markdown is supported" in drawn(message)

    await type_words(services, message, "x" * (DESCRIPTION_MAX_CHARS + 1))
    assert f"at most {DESCRIPTION_MAX_CHARS} characters" in drawn(message)
    assert advisor.reviews.proposal(proposal_id).changes[0].values["description"] == "Kept."

    await type_words(services, message, "Sport and play.")
    assert advisor.reviews.proposal(proposal_id).changes[0].values["description"] == (
        "Sport and play."
    )


async def test_a_value_proposal_is_edited_with_the_values_own_controls(e2e_harness):
    """VL-EDIT-031 — tests/brd/values.feature"""
    ids = await values(e2e_harness, "Old", "Other")
    advisor, _provider, services, message, proposal_id = await review(
        e2e_harness,
        [
            (
                "value",
                {"mode": "update", "id": ids["Old"], "name": "New", "description": "Kept.",
                 "order": 2},
            )
        ],
    )
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["✏️ Name", "📝 Description", "↩️ Back"]
    await press(services, message, "📝 Description")
    assert "Edit Value Description" in drawn(message)
    assert "Markdown is supported" in drawn(message)

    await type_words(services, message, "x" * (DESCRIPTION_MAX_CHARS + 1))
    assert f"at most {DESCRIPTION_MAX_CHARS} characters" in drawn(message)
    assert advisor.reviews.proposal(proposal_id).changes[0].values["description"] == "Kept."

    await type_words(services, message, "It keeps me well.")
    assert advisor.reviews.proposal(proposal_id).changes[0].values["description"] == (
        "It keeps me well."
    )


async def test_remind_offers_the_choices_and_other_its_schedule_offers(e2e_harness):
    """CD-EDIT-067 — tests/brd/cards.feature"""
    card_id = await run_action(e2e_harness, schedule="daily 09:00")
    advisor, _provider, services, message, proposal_id = await review(
        e2e_harness,
        [("remind", {"item": "action", "id": card_id, "minutes_before": 60})],
        agent="reminder",
    )
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["🔔 Remind", "↩️ Back"]

    await press(services, message, "🔔 Remind")
    assert message.buttons() == [
        *[("✓ " if lead == 60 else "") + remind_label(lead) for lead in (None, *REMIND_LEADS)],
        "✏️ Other",
        "↩️ Back",
    ]
    await press(services, message, "✏️ Other")
    assert REMIND_INSTRUCTION in drawn(message)
    await type_words(services, message, "30")

    assert advisor.reviews.proposal(proposal_id).changes[0].values["remind"] == 30
    assert redrawn_buttons(message) == DECISION


async def test_a_value_the_check_leaves_out_has_no_button(e2e_harness):
    """PR-EDIT-033 — tests/brd/tg_agent_shell/proposals.feature"""
    card_id = await run_action(e2e_harness)
    async with e2e_harness.sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, False)
        await session.commit()
    _advisor, _provider, services, message, _id = await review(
        e2e_harness,
        [("action", {"mode": "update", "id": card_id, "title": "Jog", "effort_points": 5})],
    )
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["✏️ Title", "↩️ Back"]


async def test_a_value_left_alone_is_not_read_again_while_safwa_types(e2e_harness):
    """PR-EDIT-034 — tests/brd/tg_agent_shell/proposals.feature"""
    card_id = await run_action(e2e_harness)
    advisor, provider = e2e_harness.advisor(
        [
            route_turn("workspace_mutator"),
            mutation_turn(
                (
                    "action",
                    {"mode": "update", "id": card_id, "title": "Jog", "schedule": "after it"},
                )
            ),
            schedule_turn(after_completion=True),
        ]
    )
    outcome = await advisor.handle("Rename Run and repeat it")
    services = review_services(e2e_harness, advisor)
    message = QueueTestMessage()
    await render_proposal(message, services, outcome.proposal_id)
    await press(services, message, "✏️ Edit")
    await press(services, message, "✏️ Title")
    asked, typing = len(provider.calls), message.bot.typing_calls

    await type_words(services, message, "Sprint")

    values = advisor.reviews.proposal(outcome.proposal_id).changes[0].values
    assert values["title"] == "Sprint"
    assert values["schedule_rule"] == {"kind": "after_completion"}
    assert len(provider.calls) == asked
    assert message.bot.typing_calls > typing


async def test_stage_turns_a_finish_into_a_move_and_back(e2e_harness):
    """CD-EDIT-068 — tests/brd/cards.feature"""
    card_id = await run_action(e2e_harness, stage="sprint")
    advisor, _provider, services, message, proposal_id = await review(
        e2e_harness,
        [("action", {"mode": "complete", "id": card_id, "tracked_mins": 15})],
        "Saved.",
        "Saved.",
    )
    change = advisor.reviews.proposal(proposal_id).changes[0]
    await press(services, message, "✏️ Edit")
    # Finishing offers Effort too, though Safwa set none (CD-DONE-072).
    assert message.buttons() == ["📍 Stage", "🔢 Effort", "⌛ Time spent", "↩️ Back"]
    await press(services, message, "📍 Stage")
    assert message.buttons() == ["Backlog", "Today", "✓ Done", "↩️ Back"]

    await press(services, message, "Backlog")
    assert change.action is ChangeAction.MOVE
    assert change.values["stage"] == "backlog" and "tracked_mins" not in change.values
    assert "<i>✍️ Edited by you:</i> 📍 Stage" in message.rendered[-1]
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["📍 Stage", "↩️ Back"]

    await press(services, message, "📍 Stage")
    await press(services, message, "Done")
    assert change.action is ChangeAction.COMPLETE and change.values["tracked_mins"] == 15
    assert "Edited by you" not in message.rendered[-1]
    await press(services, message, "✅ Update")
    async with e2e_harness.sessions() as session:
        card = await session.get(Card, card_id)
        assert (card.effective_stage, card.tracked_mins) == ("done", 15)


async def test_cd_done_072_a_finish_shows_its_time_and_effort_and_edit_sets_them(e2e_harness):
    """CD-DONE-072 — tests/brd/cards.feature"""
    async with e2e_harness.sessions() as session:
        await set_profile_field(session, ProfileField.EFFORT_TRACKING, True)
        await set_profile_field(session, ProfileField.TIME_TRACKING, True)
        card = await create_manual_card(session, title="Run", effort_points=3, stage="sprint")
        await session.commit()
        card_id = card.id
    advisor, _provider, services, message, proposal_id = await review(
        e2e_harness, [("action", {"mode": "complete", "id": card_id})], "Saved.", "Saved."
    )
    assert "<i>Effort:</i> 3" in message.rendered[-1]
    assert "<i>Time spent:</i> —" in message.rendered[-1]

    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["📍 Stage", "🔢 Effort", "⌛ Time spent", "↩️ Back"]
    await press(services, message, "🔢 Effort")
    offered = message.buttons()
    assert f"✓ {rung(3)}" in offered and offered[-2:] == [EFFORT_POINTS_ABOUT, "↩️ Back"]
    await press(services, message, EFFORT_POINTS_ABOUT)
    assert message.rendered[-1] == EFFORT_POINTS_TEXT
    await press(services, message, "↩️ Back")
    assert message.buttons() == offered
    await press(services, message, rung(5))
    assert "<i>Effort:</i> <s>3</s> <b>5</b>" in message.rendered[-1]
    assert advisor.reviews.proposal(proposal_id).changes[0].values["effort_points"] == 5

    await press(services, message, "✅ Update")
    async with e2e_harness.sessions() as session:
        card = await session.get(Card, card_id)
        assert (card.effective_stage, card.effort_points, card.tracked_mins) == ("done", 5, None)


async def test_a_time_set_by_hand_goes_once_the_action_is_not_done(e2e_harness):
    """CD-EDIT-068 — tests/brd/cards.feature"""
    card_id = await run_action(e2e_harness, stage="sprint")
    advisor, _provider, services, message, proposal_id = await review(
        e2e_harness, [("action", {"mode": "complete", "id": card_id, "tracked_mins": 15})]
    )
    change = advisor.reviews.proposal(proposal_id).changes[0]
    await press(services, message, "✏️ Edit")
    await press(services, message, "⌛ Time spent")
    await type_words(services, message, "30")
    assert change.values["tracked_mins"] == 30
    # The typed words redrew the review through the Bot API; Edit is drawn on it again.
    await render_proposal(message, services, proposal_id, editing=True)
    await press(services, message, "📍 Stage")
    await press(services, message, "Today")
    edited = [line for line in message.rendered[-1].splitlines() if "Edited by you" in line]
    assert edited == ["<i>✍️ Edited by you:</i> 📍 Stage"]
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["📍 Stage", "↩️ Back"]
    await press(services, message, "📍 Stage")
    await press(services, message, "Done")
    # The time set by hand went with Done; picking Done again brings Safwa's back.
    assert change.values["tracked_mins"] == 15


async def test_a_reopen_offers_no_done_and_a_goal_offers_no_stage(e2e_harness):
    """CD-EDIT-068 — tests/brd/cards.feature"""
    card_id = await run_action(e2e_harness, stage="sprint")
    async with e2e_harness.sessions() as session:
        await finish_action(session, card_id)
        goal = await create_card(session, kind="goal", title="Be fit")
        await session.commit()
    advisor, _provider, services, message, proposal_id = await review(
        e2e_harness, [("action", {"mode": "reopen", "id": card_id})]
    )
    await press(services, message, "✏️ Edit")
    await press(services, message, "📍 Stage")
    assert message.buttons() == ["✓ Backlog", "Sprint", "Today", "↩️ Back"]
    await press(services, message, "Today")
    change = advisor.reviews.proposal(proposal_id).changes[0]
    assert (change.action, change.values["stage"]) == (ChangeAction.REOPEN, "today")

    _advisor, _provider, _services, message, _id = await review(
        e2e_harness, [("goal", {"mode": "complete", "id": goal.id})]
    )
    assert "✏️ Edit" not in message.buttons()


async def test_a_check_proposal_is_edited_with_the_checks_own_controls(e2e_harness):
    """CH-EDIT-022 — tests/brd/checks.feature"""
    every_day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    ids = await values(e2e_harness, "Calm", "Health", "Focus")
    async with e2e_harness.sessions() as session:
        check = await create_check(session, title="Slept enough?", schedule="daily 09:00")
        await session.commit()
    advisor, provider, services, message, proposal_id = await review(
        e2e_harness,
        [
            (
                "check",
                {
                    "mode": "update",
                    "id": check.id,
                    "note": "Seven hours.",
                    "schedule": "every evening at 21:00",
                },
            )
        ],
        schedule_turn(days=every_day, time="21:00"),
    )
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["⏱ Schedule", "📝 Note", "↩️ Back"]
    await press(services, message, "📝 Note")
    assert CHECK_NOTE_INSTRUCTION in drawn(message)
    assert "Markdown is supported" in drawn(message)
    asked = len(provider.calls)
    rule = advisor.reviews.proposal(proposal_id).changes[0].values["schedule_rule"]

    await type_words(services, message, "Eight hours.")

    saved = advisor.reviews.proposal(proposal_id).changes[0].values
    assert saved["note"] == "Eight hours."
    assert saved["schedule_rule"] == rule and len(provider.calls) == asked

    async with e2e_harness.sessions() as session:
        await toggle_check_value(session, check.id, ids["Calm"])
        await session.commit()
    _advisor, _provider, services, message, _id = await review(
        e2e_harness, [("check", {"mode": "link", "id": check.id, "values": ["Health"]})]
    )
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["💎 Values", "↩️ Back"]
    await press(services, message, "💎 Values")
    assert message.rendered[-1] == "<b>Link Values</b>"
    assert message.buttons() == ["✓ Health", "Focus", "↩️ Back"]
    _advisor, _provider, services, message, _id = await review(
        e2e_harness,
        [("check", {"mode": "update", "id": check.id, "title": "Slept well?", "note": "Nine."})],
    )
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["📝 Note", "↩️ Back"]
    _advisor, _provider, services, message, _id = await review(
        e2e_harness,
        [("remind", {"item": "check", "id": check.id, "minutes_before": 15})],
        agent="reminder",
    )
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["🔔 Remind", "↩️ Back"]
    _advisor, _provider, _services, message, _id = await review(
        e2e_harness, [("check", {"mode": "passed", "id": check.id})]
    )
    assert "✏️ Edit" not in message.buttons()


async def test_a_trackers_schedule_set_to_off_takes_its_remind_with_it(e2e_harness):
    """CH-EDIT-022 — tests/brd/checks.feature"""
    every_day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    advisor, _provider = e2e_harness.advisor(
        [
            *reminder_script(
                {"days": every_day, "time": "21:00"}, remind_text="Go to bed.", when="every day at 21"
            ),
            reminder_turn(("set_tracker", {"title": "In bed by 21?", "values": []}), prefix="tracker"),
        ]
    )
    outcome = await advisor.handle("Remind me to go to bed every day at 21")
    async with e2e_harness.sessions() as session:
        await advisor.switch_proposal(session, outcome.proposal_id, 0)
    services = review_services(e2e_harness, advisor)
    message = QueueTestMessage()
    await render_proposal(message, services, outcome.proposal_id, editing=True)
    assert "🔔 Remind" in message.buttons()

    await press(services, message, "⏱ Schedule")
    await type_words(services, message, "off")

    saved = advisor.reviews.proposal(outcome.proposal_id).changes[0].values
    assert saved["schedule"] is None and "remind" not in saved
    await render_proposal(message, services, outcome.proposal_id, editing=True)
    assert "🔔 Remind" not in message.buttons()


async def diary_review(e2e_harness, *calls: dict):
    advisor, _provider = e2e_harness.advisor(
        [
            diary_turn(("route", {"name": "diary"})),
            diary_turn(("read_day", {})),
            *[diary_turn(("diary", call)) for call in calls],
        ],
        subagents=(diary_subagent(e2e_harness),),
    )
    outcome = await advisor.handle("Write the day down")
    services = review_services(e2e_harness, advisor)
    message = QueueTestMessage()
    await render_proposal(message, services, outcome.proposal_id)
    return advisor, services, message, outcome.proposal_id


async def test_a_day_put_up_for_approval_is_edited_before_it_is_saved(e2e_harness):
    """DI-EDIT-029 — tests/brd/diary.feature"""
    advisor, services, message, proposal_id = await diary_review(
        e2e_harness,
        {
            "mode": "update",
            "date": DIARY_TODAY,
            "body": "Market closed.",
            "remark": "One thing held.",
            "feeling_score": 7,
        },
    )
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["🙂 Feeling", "📝 Entry", "↩️ Back"]

    await press(services, message, "🙂 Feeling")
    assert message.buttons() == [
        *[("✓ " if score == 7 else "") + f"{score} {face}" for score, face in FEELING.items()],
        "↩️ Back",
    ]
    await press(services, message, f"3 {FEELING[3]}")
    assert advisor.reviews.proposal(proposal_id).changes[0].values["feeling_score"] == 3

    await press(services, message, "✏️ Edit")
    await press(services, message, "📝 Entry")
    assert "Markdown is supported" in drawn(message)
    await type_words(services, message, "   ")
    assert "Entry cannot be empty." in drawn(message)
    await type_words(services, message, "Market opened.")
    values = advisor.reviews.proposal(proposal_id).changes[0].values
    assert (values["body"], values["feeling_score"]) == ("Market opened.", 3)

    await render_proposal(message, services, proposal_id, editing=True)
    assert message.buttons() == ["🙂 Feeling", "📝 Entry", "↩️ Back"]
    assert "<i>One thing held.</i>" in drawn(message)
    assert advisor.reviews.proposal(proposal_id).changes[0].values["remark"] == "One thing held."
    [record] = [
        tool
        for tool in advisor.reviews.batch_for_proposal(proposal_id).tool_calls
        if tool.get("proposal_id") == proposal_id
    ]
    assert record["change"]["values"]["entry_date"] == DIARY_TODAY

    async with e2e_harness.sessions() as session:
        await create_diary_entry(session, entry_date=date.today(), body="To go.")
        await session.commit()
    _advisor, _services, message, _id = await diary_review(
        e2e_harness, {"mode": "delete", "date": DIARY_TODAY}
    )
    assert "✏️ Edit" not in message.buttons()


async def test_rm_edit_038_a_count_of_answers_is_not_read_again_with_new_words(e2e_harness):
    """RM-EDIT-038 — tests/brd/reminders.feature"""
    async with e2e_harness.sessions() as session:
        check = await create_check(session, title="Sat straight?", schedule="after completion")
        await session.commit()
        named = check.id
    advisor, provider = e2e_harness.advisor(condition_turns(f"Check #{named}"))
    outcome = await advisor.handle("cheer me on at every 3 misses of posture")
    services = review_services(e2e_harness, advisor)
    message = QueueTestMessage()
    await render_proposal(message, services, outcome.proposal_id)
    await press(services, message, "✏️ Edit")
    await press(services, message, "✏️ Text")
    asked = len(provider.calls)

    await type_words(services, message, "Tell me to sit straight.")

    # What it counts was read from the call, which the owner left as it was (PR-EDIT-034).
    assert len(provider.calls) == asked
    values = advisor.reviews.proposal(outcome.proposal_id).changes[0].values
    assert values["remind_text"] == "Tell me to sit straight."
    assert values["schedule_text"] == f"every 3 Missed of Check #{named}"


async def test_a_reminder_proposals_words_are_read_again_with_its_time(e2e_harness):
    """RM-EDIT-038 — tests/brd/reminders.feature"""
    config = {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "time": "08:30"}
    advisor, provider = e2e_harness.advisor(
        [
            *reminder_script(config, remind_text="Ask what to start.", when="weekdays at 8:30"),
            reminder_turn(("set_reminder_config", config), prefix="again"),
            reminder_turn(("not_clear_enough", {"reason": "No hour.", "about": "time"}), prefix="no"),
        ]
    )
    outcome = await advisor.handle("remind me each weekday morning")
    services = review_services(e2e_harness, advisor)
    message = QueueTestMessage()
    await render_proposal(message, services, outcome.proposal_id)
    await press(services, message, "✏️ Edit")
    assert message.buttons() == ["✏️ Text", "↩️ Back"]
    await press(services, message, "✏️ Text")
    assert html.escape(REMINDER_TEXT_INSTRUCTION) in drawn(message)
    asked = len(provider.calls)

    await type_words(services, message, "Ask what to finish.")

    assert len(provider.calls) == asked + 1
    assert "Timing phrase: weekdays at 8:30" in json.dumps(provider.calls[-1])
    assert "Ask what to finish." not in json.dumps(provider.calls[-1])
    values = advisor.reviews.proposal(outcome.proposal_id).changes[0].values
    assert values["remind_text"] == "Ask what to finish."

    # The typed words redrew the review through the Bot API; Edit is drawn on it again.
    await render_proposal(message, services, outcome.proposal_id, editing=True)
    await press(services, message, "✏️ Text")
    await type_words(services, message, "Ask about tomorrow.")
    assert "No hour." in drawn(message)
    values = advisor.reviews.proposal(outcome.proposal_id).changes[0].values
    assert values["remind_text"] == "Ask what to finish."
