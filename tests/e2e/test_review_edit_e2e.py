from __future__ import annotations

import json
from datetime import timedelta

import pytest
from advisor_e2e_helpers import create_manual_card, mutation_turn, route_turn, schedule_turn
from review_e2e_helpers import QueueTestCallback, QueueTestMessage, review_services
from sqlalchemy import select
from ui_harness import kind_of

from safwa.bootstrap.modules import PROPOSALS
from safwa.constants import DESCRIPTION_MAX_CHARS
from safwa.features.cards.model import EFFORT_RUNGS, Card, effort_label
from safwa.features.cards.telegram import render_card_choices
from safwa.features.cards.telegram.selectors import CHOICE_TITLES
from safwa.features.cards.use_cases import delete_card, toggle_card_value
from safwa.features.profile.model import ProfileField
from safwa.features.profile.use_cases import set_profile_field
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


async def review(e2e_harness, calls, *answers: str, request: str = "Change it"):
    """Safwa's proposal from `calls`, drawn as its review screen."""
    advisor, provider = e2e_harness.advisor(
        [route_turn("workspace_mutator"), mutation_turn(*calls), *answers]
    )
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
        change = ProposalChange("card", ChangeAction.UPDATE, 1, source=call)
        fields = PROPOSALS.edit_fields([change])
        return [field.label for _i, _k, field in fields if field.markdown or not markdown]

    assert labels("action") == [
        "✏️ Title",
        "📝 Note",
        "📍 Stage",
        "⚠️ Priority",
        "⏱ Schedule",
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
    """VL-EDIT-030 — tests/brd/values.feature"""
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
