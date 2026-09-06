"""What every Telegram screen test needs: the fakes, the container, and where the UI lives."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

import safwa
import safwa.features.planning.telegram.plan as plan_module
from safwa.bootstrap.modules import (
    AI_VIEWS,
    ALLOWED_VIEWS,
    FEATURE_CALLBACK_ACTIONS,
    FEATURE_COMMANDS,
    FEATURE_TEXT_INPUTS,
    PROPOSALS,
    SCREENS,
)
from safwa.features.cards.use_cases import create_card
from telegram_llm import ChatHost, TranscriptionError, TranscriptionResult
from tg_agent_shell.ai.sql import create_ai_views
from tg_agent_shell.foundation.kinds import MARKS
from tg_agent_shell.history import TelegramNotes
from tg_agent_shell.proposals.api import ProposalDescription
from tg_agent_shell.proposals.store import ProposalStore
from tg_agent_shell.telegram import SHELL_COMMANDS
from tg_agent_shell.telegram.model import UiSession
from tg_agent_shell.turn import TurnManager

# What the composition root puts together, which is what a live Safwa answers with.
CALLBACK_ACTIONS = FEATURE_CALLBACK_ACTIONS


def spawn_timer(work: Coroutine[None, None, None], name: str) -> asyncio.Task[None]:
    """The Toast timer a test's host starts: no test shuts down, so no test cancels one."""
    return asyncio.create_task(work, name=name)


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


def services_for(sessions, *, root=None, reviews=None, transcriber=None):
    return SimpleNamespace(
        sessions=sessions,
        owner_id=42,
        turn=TurnManager(),
        screens=SCREENS,
        chat=ChatHost(TelegramNotes(sessions), MARKS, spawn=spawn_timer),
        commands=(*FEATURE_COMMANDS, *SHELL_COMMANDS),
        callback_actions=CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
        views=ALLOWED_VIEWS,
        after_turn=(),
        bot_username="safwa_ai_bot",
        root=root
        if root is not None
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


async def seed_plan(sessions) -> dict[str, int]:
    # The link-tap counter is per process, so one test's taps would otherwise count in the next.
    plan_module._link_taps.clear()
    async with sessions() as session:
        await (await session.connection()).run_sync(
            lambda connection: create_ai_views(connection, AI_VIEWS)
        )
        ids = {
            "sprint": (
                await create_card(
                    session, kind="action", title="Ship it", stage="sprint", effort_points=3
                )
            ).id,
            "pick": (
                await create_card(session, kind="action", title="Pick me", effort_points=1)
            ).id,
            "skip": (
                await create_card(session, kind="action", title="Skip me", effort_points=2)
            ).id,
        }
        await session.commit()
    return ids


async def plan_filters(sessions) -> list[int]:
    async with sessions() as session:
        ui = await session.scalar(select(UiSession).where(UiSession.kind == "sprint_plan"))
        return list(ui.state["filters"])


class ScriptedTranscriber:
    """The ASR network boundary: one canned transcript, or one failure."""

    def __init__(
        self, text: str = "", error: str = "", progress_at: tuple[float, ...] = ()
    ) -> None:
        self.text = text
        self.error = error
        self.progress_at = progress_at
        self.clips: list[object] = []

    async def transcribe(self, clip, *, progress=None):
        self.clips.append(clip)
        for done in self.progress_at:
            if progress is not None:
                await progress(done, clip.duration_seconds)
        if self.error:
            raise TranscriptionError(self.error)
        return TranscriptionResult(text=self.text, elapsed_seconds=0.1)

    async def close(self) -> None:
        return None


def voice_message_for(
    message_id: int, *, duration: int = 12, file_size: int = 4_096
) -> FakeMessage:
    return FakeMessage(
        message_id,
        bot_message=False,
        answer_as_new=True,
        voice=SimpleNamespace(
            file_id=f"voice-{message_id}",
            duration=duration,
            file_size=file_size,
            mime_type="audio/ogg",
        ),
    )


def capture_dialogue_turns(monkeypatch) -> list[tuple[str, object]]:
    """Stop at the handler's edge: the advisor loop itself is the text path's test."""
    import tg_agent_shell.telegram.dialogue as dialogue_module

    turns: list[tuple[str, object]] = []

    async def fake_turn(_message, _services, request, source):
        turns.append((request, source))

    monkeypatch.setattr(dialogue_module, "run_dialogue_turn", fake_turn)
    return turns
