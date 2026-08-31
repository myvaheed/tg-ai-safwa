"""What every Telegram screen test needs: the fakes, the container, and where the UI lives."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import safwa
from safwa.bootstrap.modules import (
    ALLOWED_VIEWS,
    FEATURE_CALLBACK_ACTIONS,
    FEATURE_COMMANDS,
    FEATURE_TEXT_INPUTS,
    PROPOSALS,
    SCREENS,
)
from safwa.features.proposals.api import ProposalDescription
from safwa.features.proposals.store import ProposalStore
from safwa.history import MARKS, TelegramNotes
from safwa.telegram import SHELL_CALLBACK_ACTIONS, SHELL_COMMANDS
from safwa.turn import TurnManager
from telegram_llm import ChatHost

# What the composition root puts together, which is what a live Safwa answers with.
CALLBACK_ACTIONS = {**SHELL_CALLBACK_ACTIONS, **FEATURE_CALLBACK_ACTIONS}


class FakeBot:
    def __init__(self) -> None:
        self.id = 999
        self.downloads: list[str] = []
        self.audio_bytes = b"OggS-fake-audio"
        self.edits: list[tuple[int, str, object | None]] = []
        self.deleted: list[int] = []
        self.deleted_batches: list[list[int]] = []
        self.cleared_markup: list[int] = []
        self.published_commands: list[list[str]] = []

    async def edit_message_text(
        self,
        text: str | None = None,
        *,
        chat_id: int,
        message_id: int,
        reply_markup=None,
        parse_mode=None,
        rich_message=None,
    ) -> None:
        del chat_id, parse_mode
        body = text if rich_message is None else rich_message.html
        self.edits.append((message_id, body, reply_markup))

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        del chat_id
        self.deleted.append(message_id)

    async def delete_messages(self, *, chat_id: int, message_ids: list[int]) -> None:
        del chat_id
        self.deleted_batches.append(message_ids)

    async def edit_message_reply_markup(
        self, *, chat_id: int, message_id: int, reply_markup=None
    ) -> None:
        del chat_id, reply_markup
        self.cleared_markup.append(message_id)

    async def send_chat_action(self, chat_id: int, action) -> None:
        del chat_id, action

    async def download(self, file_id: str, destination):
        self.downloads.append(file_id)
        destination.write(self.audio_bytes)
        return destination

    async def set_my_commands(self, commands) -> None:
        self.published_commands.append([command.command for command in commands])


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        *,
        text: str = "",
        bot_message: bool,
        bot: FakeBot | None = None,
        chat_id: int = 700,
        answer_as_new: bool = False,
        voice: SimpleNamespace | None = None,
    ) -> None:
        self.message_id = message_id
        self.text = text
        self.voice = voice
        self.audio = None
        self.video_note = None
        self.bot = bot or FakeBot()
        self.chat = SimpleNamespace(id=chat_id, type="private")
        self.from_user = SimpleNamespace(id=42, is_bot=bot_message, full_name="Name Surname")
        self.date = datetime.now(UTC)
        self.edits: list[tuple[str, object | None]] = []
        self.answers: list[str] = []
        self.answer_markups: list[object | None] = []
        self.was_deleted = False
        self.answer_as_new = answer_as_new
        self.sent_messages: list[FakeMessage] = []

    async def edit_text(
        self, text: str | None = None, *, reply_markup=None, parse_mode=None, rich_message=None
    ):
        del parse_mode
        self.edits.append((text if rich_message is None else rich_message.html, reply_markup))
        return self

    async def answer(self, text: str, *, reply_markup=None, parse_mode=None):
        del parse_mode
        self.answers.append(text)
        self.answer_markups.append(reply_markup)
        if self.answer_as_new:
            # Telegram hands out a fresh id per message; a repeated one would collapse
            # several registrations into one row.
            sent = FakeMessage(
                self.message_id + 1_000 + len(self.sent_messages),
                text=text,
                bot_message=True,
                bot=self.bot,
                chat_id=self.chat.id,
            )
            self.sent_messages.append(sent)
            return sent
        return self

    async def answer_rich(self, *, rich_message, reply_markup=None):
        return await self.answer(rich_message.html, reply_markup=reply_markup)

    async def delete(self) -> None:
        self.was_deleted = True


class FakeCallback:
    def __init__(self, token: str, message: FakeMessage) -> None:
        self.data = f"cb:{token}"
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class StubAdvisor:
    """Only the hooks a dismissed proposal screen reaches for, plus the real registry."""

    proposals = PROPOSALS

    def __init__(self, reviews: ProposalStore | None = None) -> None:
        self.reviews = reviews if reviews is not None else ProposalStore()

    async def describe_proposal(self, _session, _proposal_id) -> ProposalDescription:
        return ProposalDescription(summary="Rename Tag “Family”", fields=["Name: Home → Family"])

    async def cancel_approval_for_proposal(self, _proposal_id) -> str | None:
        return None


def services_for(sessions, *, advisor=None, reviews=None, transcriber=None):
    return SimpleNamespace(
        sessions=sessions,
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(sessions), MARKS),
        commands=(*SHELL_COMMANDS, *FEATURE_COMMANDS),
        callback_actions=CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
        views=ALLOWED_VIEWS,
        bot_username="safwa_ai_bot",
        advisor=advisor
        if advisor is not None
        else SimpleNamespace(
            proposals=PROPOSALS, reviews=reviews if reviews is not None else ProposalStore()
        ),
        transcriber=transcriber,
    )


def button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def ui_sources() -> list[Path]:
    """Every module that draws a Telegram screen.

    The inline-button invariants were single-module when the UI lived in one file. The
    screens are feature-owned now, so the scan asks which modules reach for aiogram
    rather than reading a list a new screen has to be added to.
    """
    root = Path(safwa.__file__).parent
    return [
        path
        for package in ("features", "shell", "telegram")
        for path in sorted((root / package).rglob("*.py"))
        if "aiogram" in path.read_text(encoding="utf-8")
    ]
