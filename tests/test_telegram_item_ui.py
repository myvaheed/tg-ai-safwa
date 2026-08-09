from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from safwa.ai.context import DialogueMessage
from safwa.ai.service import AIOutcome
from safwa.drafts import DraftService
from safwa.enums import DraftStatus, MessageKind
from safwa.models import (
    CallbackToken,
    Card,
    CardDraft,
    CardDraftBundle,
    ChangeProposal,
    ProposalChange,
    Tag,
    TelegramMessage,
    UiSession,
    Value,
    Workspace,
)
from safwa.telegram import (
    GenerationGuard,
    OwnerAndWritingMiddleware,
    callback_token_handler,
    dismiss_prior_ui,
    ordinary_text,
    render_draft,
    render_item_editor,
    render_item_text_prompt,
    render_proposal,
)


class FakeBot:
    def __init__(self) -> None:
        self.edits: list[tuple[int, str, object | None]] = []
        self.deleted: list[int] = []
        self.cleared_markup: list[int] = []

    async def edit_message_text(
        self,
        text: str,
        *,
        chat_id: int,
        message_id: int,
        reply_markup=None,
        parse_mode=None,
    ) -> None:
        del chat_id, parse_mode
        self.edits.append((message_id, text, reply_markup))

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        del chat_id
        self.deleted.append(message_id)

    async def edit_message_reply_markup(
        self, *, chat_id: int, message_id: int, reply_markup=None
    ) -> None:
        del chat_id, reply_markup
        self.cleared_markup.append(message_id)

    async def send_chat_action(self, chat_id: int, action) -> None:
        del chat_id, action


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        *,
        text: str = "",
        bot_message: bool,
        bot: FakeBot | None = None,
        chat_id: int = 700,
    ) -> None:
        self.message_id = message_id
        self.text = text
        self.bot = bot or FakeBot()
        self.chat = SimpleNamespace(id=chat_id, type="private")
        self.from_user = SimpleNamespace(id=42, is_bot=bot_message)
        self.date = datetime.now(UTC)
        self.edits: list[tuple[str, object | None]] = []
        self.answers: list[str] = []
        self.was_deleted = False

    async def edit_text(self, text: str, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.edits.append((text, reply_markup))
        return self

    async def answer(self, text: str, *, reply_markup=None, parse_mode=None):
        del reply_markup, parse_mode
        self.answers.append(text)
        return self

    async def delete(self) -> None:
        self.was_deleted = True


class FakeCallback:
    def __init__(self, token: str, message: FakeMessage) -> None:
        self.data = f"cb:{token}"
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


def services_for(sessions):
    return SimpleNamespace(sessions=sessions, owner_id=42, guard=GenerationGuard())


def button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_commands_are_deleted_except_newsession(sessions, monkeypatch) -> None:
    import safwa.telegram as telegram_module

    monkeypatch.setattr(telegram_module, "Message", FakeMessage)
    middleware = OwnerAndWritingMiddleware()
    services = services_for(sessions)
    handled: list[str] = []

    async def handler(event, _data):
        handled.append(event.text)

    for index, command in enumerate(("/start", "/mem remember this", "/cancel"), start=1):
        message = FakeMessage(index, text=command, bot_message=False)
        await middleware(handler, message, {"services": services})
        assert message.was_deleted is True

    boundary = FakeMessage(10, text="/newsession Initial request", bot_message=False)
    await middleware(handler, boundary, {"services": services})

    assert boundary.was_deleted is False
    assert handled == [
        "/start",
        "/mem remember this",
        "/cancel",
        "/newsession Initial request",
    ]


async def test_proposal_ui_releases_generation_guard_before_continuity_work(sessions) -> None:
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Create VrWalk",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="tag",
                action="create",
                values={"name": "VrWalk"},
            )
        )
        await session.commit()
        proposal_id = proposal.id

    class Advisor:
        async def handle(self, *_args, **_kwargs):
            return AIOutcome(
                "proposal",
                "I prepared the proposed changes for your approval.",
                proposal_id=proposal_id,
            )

    class History:
        async def dialogue(self, *_args, **_kwargs):
            return [DialogueMessage(role="user", content="[Initial request]: Create a Tag")]

    guard = GenerationGuard()

    class Continuity:
        called = False

        async def maybe_summarize(self, *_args, **_kwargs):
            self.called = True
            assert guard.active is False

    continuity = Continuity()
    services = SimpleNamespace(
        sessions=sessions,
        owner_id=42,
        guard=guard,
        advisor=Advisor(),
        history=History(),
        continuity=continuity,
    )
    message = FakeMessage(20, text="Create a Tag VrWalk", bot_message=False)

    await ordinary_text(message, services)

    assert continuity.called is True
    assert guard.active is False
    async with sessions() as session:
        tokens = list(
            await session.scalars(
                select(CallbackToken).where(
                    CallbackToken.action.in_(["proposal_approve", "proposal_reject"])
                )
            )
        )
        assert len(tokens) == 2


async def test_new_dialogue_discards_and_freezes_pending_proposal(sessions) -> None:
    async with sessions() as session:
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Rename the Tag",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="tag",
                action="update",
                entity_id=9,
                expected_version=1,
                values={"name": "Family"},
            )
        )
        session.add_all(
            [
                TelegramMessage(
                    chat_id=700,
                    message_id=10,
                    direction="out",
                    kind=MessageKind.APPROVAL.value,
                    related_id=proposal.id,
                ),
                TelegramMessage(
                    chat_id=700,
                    message_id=9,
                    direction="out",
                    kind=MessageKind.DASHBOARD.value,
                ),
            ]
        )
        await session.commit()
        proposal_id = proposal.id

    bot = FakeBot()
    incoming = FakeMessage(11, text="Another question", bot_message=False, bot=bot)
    await dismiss_prior_ui(incoming, services_for(sessions))

    assert bot.deleted == [9]
    assert bot.edits[0][0] == 10
    assert "Proposal discarded" in bot.edits[0][1]
    assert "name=" in bot.edits[0][1]
    assert "Family" in bot.edits[0][1]
    async with sessions() as session:
        proposal = await session.get(ChangeProposal, proposal_id)
        assert proposal.status == "rejected"
        frozen = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == 10)
        )
        assert frozen.kind == MessageKind.DIALOGUE_ASSISTANT.value
        assert (
            await session.scalar(select(TelegramMessage).where(TelegramMessage.message_id == 9))
            is None
        )


async def test_new_dialogue_discards_complete_ai_draft_bundle(sessions) -> None:
    async with sessions() as session:
        value = Value(name="Reliability", description="Keep releases dependable")
        tag = Tag(name="VrWalk", description="VR walking project")
        blocker = Card(
            parent_id=None,
            kind="action",
            title="Get production access",
            note="",
            effort_points=2,
        )
        session.add_all([value, tag, blocker])
        await session.flush()
        bundle = await DraftService(session).create_bundle(
            "ai",
            [
                {
                    "kind": "goal",
                    "title": "Release",
                    "note": "Ship VrWalk safely",
                    "stage": "today",
                    "priority": "critical",
                    "hard_time": True,
                    "root_confirmed": True,
                    "draft_ref": "release-goal",
                    "field_provenance": {"note": "inferred from the request"},
                },
                {
                    "kind": "action",
                    "title": "Deploy",
                    "note": "Publish the Android client",
                    "stage": "sprint",
                    "priority": "low",
                    "hard_time": False,
                    "effort_points": 5,
                    "repeatable": True,
                    "categories": ["work"],
                    "energy_types": ["cognitive"],
                    "value_ids": [value.id],
                    "tag_ids": [tag.id],
                    "dependencies": [{"card_id": blocker.id, "copy_to_repeat": True}],
                    "parent_draft_ref": "release-goal",
                    "field_provenance": {"effort_points": "AI estimate"},
                },
            ],
        )
        drafts = await DraftService(session).get_bundle_drafts(bundle.id)
        session.add(
            TelegramMessage(
                chat_id=700,
                message_id=20,
                direction="out",
                kind=MessageKind.DRAFT_REVIEW.value,
                related_id=drafts[0].id,
            )
        )
        await session.commit()
        bundle_id = bundle.id

    bot = FakeBot()
    await dismiss_prior_ui(
        FakeMessage(21, text="Continue", bot_message=False, bot=bot),
        services_for(sessions),
    )

    assert "Create Goal: Release" in bot.edits[0][1]
    assert "Create Action: Deploy" in bot.edits[0][1]
    discarded_text = bot.edits[0][1]
    assert "Parent: Release (proposed Goal)" in discarded_text
    assert "Stage: Today" in discarded_text
    assert "Note: Ship VrWalk safely" in discarded_text
    assert "Priority: Critical" in discarded_text
    assert "Hard Time: Yes" in discarded_text
    assert "Effort: 5" in discarded_text
    assert "Repeatable: Yes" in discarded_text
    assert "Categories: work" in discarded_text
    assert "Energy: cognitive" in discarded_text
    assert "Values: Reliability" in discarded_text
    assert "Tags: VrWalk" in discarded_text
    assert "Blockers: Get production access (copy to repeat)" in discarded_text
    assert "AI assumptions: effort_points: AI estimate" in discarded_text
    assert "Unresolved / validation: —" in discarded_text
    async with sessions() as session:
        bundle = await session.get(CardDraftBundle, bundle_id)
        assert bundle.status == DraftStatus.DISCARDED.value
        drafts = await DraftService(session).get_bundle_drafts(bundle_id)
        assert {draft.status for draft in drafts} == {DraftStatus.DISCARDED.value}


async def test_explicit_ai_creation_discard_keeps_proposed_fields(sessions) -> None:
    token = "discard-create"
    async with sessions() as session:
        bundle = await DraftService(session).create_bundle(
            "ai",
            [
                {
                    "kind": "action",
                    "title": "Walk outside",
                    "note": "Take the quiet route",
                    "stage": "today",
                    "priority": "critical",
                    "hard_time": True,
                    "effort_points": 3,
                    "repeatable": True,
                    "categories": ["rest"],
                    "energy_types": ["physical"],
                    "root_confirmed": True,
                }
            ],
        )
        draft = (await DraftService(session).get_bundle_drafts(bundle.id))[0]
        session.add(
            CallbackToken(
                token=token,
                owner_id=42,
                action="draft_discard",
                payload={"id": draft.id},
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await session.commit()
        bundle_id = bundle.id

    message = FakeMessage(25, bot_message=True)
    await callback_token_handler(FakeCallback(token, message), services_for(sessions))

    assert len(message.edits) == 1
    discarded_text = message.edits[0][0]
    assert "Proposal discarded" in discarded_text
    assert "Nothing was saved" in discarded_text
    assert "Create Action: Walk outside" in discarded_text
    assert "Note: Take the quiet route" in discarded_text
    assert "Stage: Today" in discarded_text
    assert "Priority: Critical" in discarded_text
    assert "Hard Time: Yes" in discarded_text
    assert "Effort: 3" in discarded_text
    assert "Repeatable: Yes" in discarded_text
    assert "Categories: rest" in discarded_text
    assert "Energy: physical" in discarded_text
    async with sessions() as session:
        bundle = await session.get(CardDraftBundle, bundle_id)
        assert bundle.status == DraftStatus.DISCARDED.value


async def test_tag_field_input_reuses_editor_message_and_deletes_input(sessions) -> None:
    services = services_for(sessions)
    callback = FakeMessage(30, bot_message=True)
    await render_item_editor(callback, services, "tag", mode="create")
    await render_item_text_prompt(
        callback,
        services,
        entity="tag",
        mode="create",
        item_id=None,
        field="name",
    )

    user_input = FakeMessage(
        31,
        text="Family",
        bot_message=False,
        bot=callback.bot,
    )
    await ordinary_text(user_input, services)

    assert user_input.was_deleted is True
    assert user_input.answers == []
    assert callback.bot.edits[-1][0] == 30
    assert "Name: Family" in callback.bot.edits[-1][1]
    async with sessions() as session:
        ui = await session.scalar(select(UiSession).where(UiSession.owner_id == 42))
        assert ui.kind == "item_editor"
        assert ui.state["values"]["name"] == "Family"
        classified = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.message_id == 31)
        )
        assert classified.kind == MessageKind.UI_INPUT.value


async def test_card_note_input_updates_same_review_message(sessions) -> None:
    async with sessions() as session:
        bundle = await DraftService(session).create_bundle(
            "manual",
            [
                {
                    "kind": "action",
                    "title": "Run",
                    "root_confirmed": True,
                    "effort_points": 2,
                }
            ],
        )
        draft = (await DraftService(session).get_bundle_drafts(bundle.id))[0]
        session.add(
            UiSession(
                owner_id=42,
                kind="draft_text",
                state={"draft_id": draft.id, "field": "note", "message_id": 40},
                expires_at=datetime.now(UTC).replace(year=2030),
            )
        )
        await session.commit()
        draft_id = draft.id

    bot = FakeBot()
    user_input = FakeMessage(41, text="Weekdays", bot_message=False, bot=bot)
    await ordinary_text(user_input, services_for(sessions))

    assert user_input.was_deleted is True
    assert bot.edits[-1][0] == 40
    assert "Note: Weekdays" in bot.edits[-1][1]
    async with sessions() as session:
        draft = await session.get(CardDraft, draft_id)
        assert draft.note == "Weekdays"


async def test_ai_and_manual_draft_footers_follow_origin(sessions) -> None:
    async with sessions() as session:
        ai_bundle = await DraftService(session).create_bundle(
            "ai", [{"kind": "goal", "title": "AI Goal", "root_confirmed": True}]
        )
        manual_bundle = await DraftService(session).create_bundle(
            "manual", [{"kind": "goal", "title": "Manual Goal", "root_confirmed": True}]
        )
        ai_id = (await DraftService(session).get_bundle_drafts(ai_bundle.id))[0].id
        manual_id = (await DraftService(session).get_bundle_drafts(manual_bundle.id))[0].id
        await session.commit()

    services = services_for(sessions)
    ai_message = FakeMessage(50, bot_message=True)
    await render_draft(ai_message, services, ai_id)
    ai_buttons = button_texts(ai_message.edits[-1][1])
    assert "✅ Save" in ai_buttons
    assert "🗑 Discard" in ai_buttons
    assert "↩️ Back" not in ai_buttons

    manual_message = FakeMessage(51, bot_message=True)
    await render_draft(manual_message, services, manual_id)
    manual_buttons = button_texts(manual_message.edits[-1][1])
    assert "✅ Create" in manual_buttons
    assert "↩️ Back" in manual_buttons


async def test_item_proposal_shows_diffs_and_only_save_discard_footer(sessions) -> None:
    async with sessions() as session:
        tag = Tag(name="Family", description="Old description")
        session.add(tag)
        await session.flush()
        workspace = await session.get(Workspace, 1)
        proposal = ChangeProposal(
            message="Improve the Family Tag",
            workspace_revision=workspace.revision,
            status="pending",
        )
        session.add(proposal)
        await session.flush()
        session.add(
            ProposalChange(
                proposal_id=proposal.id,
                position=0,
                entity="tag",
                action="update",
                entity_id=tag.id,
                expected_version=tag.version,
                values={"description": "Relationships and home"},
            )
        )
        await session.commit()
        proposal_id = proposal.id

    message = FakeMessage(60, bot_message=True)
    await render_proposal(message, services_for(sessions), proposal_id)
    text, markup = message.edits[-1]
    assert "Old description" in text
    assert "Relationships and home" in text
    assert "→" in text
    buttons = button_texts(markup)
    assert buttons[-2:] == ["✅ Save", "🗑 Discard"]
    assert "↩️ Back" not in buttons
