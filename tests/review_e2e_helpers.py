from __future__ import annotations

from types import SimpleNamespace

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from ui_harness import spawn_timer

from safwa.bootstrap.modules import FEATURE_CALLBACK_ACTIONS, FEATURE_TEXT_INPUTS, SCREENS
from telegram_llm import ChatHost
from tg_agent_shell.foundation.kinds import MARKS
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.telegram import callback_token_handler
from tg_agent_shell.telegram.model import CallbackToken
from tg_agent_shell.turn import TurnManager


class QueueTestBot:
    def __init__(self) -> None:
        self.typing_calls = 0
        self.edits: list[str] = []
        self.deleted: list[int] = []

    async def send_chat_action(self, _chat_id, _action) -> None:
        self.typing_calls += 1

    async def edit_message_text(
        self, text, *, chat_id, message_id, reply_markup=None, parse_mode=None
    ) -> None:
        del chat_id, message_id, reply_markup, parse_mode
        self.edits.append(text)

    async def delete_message(self, chat_id, message_id) -> None:
        del chat_id
        self.deleted.append(message_id)

    async def edit_message_reply_markup(self, *, chat_id, message_id, reply_markup=None) -> None:
        del chat_id, message_id, reply_markup


class QueueTestMessage:
    def __init__(self) -> None:
        self.message_id = 900
        self.chat = SimpleNamespace(id=700, type="private")
        self.from_user = SimpleNamespace(id=42, is_bot=True)
        self.bot = QueueTestBot()
        self.text = ""
        self.rendered: list[str] = []
        self.markups: list[InlineKeyboardMarkup | None] = []

    async def edit_text(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        return self

    async def answer(self, text, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.rendered.append(text)
        self.markups.append(reply_markup)
        return self

    def buttons(self) -> list[str]:
        markup = self.markups[-1]
        return [button.text for row in markup.inline_keyboard for button in row]


class QueueTestCallback:
    def __init__(self, token: str, message: QueueTestMessage) -> None:
        self.data = f"cb:{token}"
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text=None, *, show_alert=False) -> None:
        self.answers.append((text, show_alert))


class QueueTestHistory:
    async def dialogue(self, _chat_id):
        raise AssertionError("approval resume must use its persisted dialogue")


async def resolve_queued_proposal(
    e2e_harness, services, message, proposal_id: int, action: str
) -> None:
    async with e2e_harness.sessions() as session:
        token = next(
            candidate
            for candidate in await session.scalars(
                select(CallbackToken).where(
                    CallbackToken.action == action,
                    CallbackToken.consumed_at.is_(None),
                )
            )
            if candidate.payload["id"] == proposal_id
        )
    await callback_token_handler(QueueTestCallback(token.token, message), services)


async def standalone_tag_proposal(e2e_harness, advisor, name: str) -> int:
    """A proposal with no live approval batch, which is the plain receipt path."""
    outcome = await advisor.handle(f"Create a {name} tag")
    assert outcome.proposal_id is not None
    async with e2e_harness.sessions() as session:
        [e2e_harness.reviews.close_batch(batch) for batch in e2e_harness.reviews.open_batches]
        await session.commit()
    return outcome.proposal_id


def review_services(e2e_harness, advisor) -> SimpleNamespace:
    return SimpleNamespace(
        sessions=e2e_harness.sessions,
        root=advisor,
        history=QueueTestHistory(),
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(e2e_harness.sessions), MARKS, spawn=spawn_timer),
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
    )
