"""The example application, assembled the way its own composition root assembles it.

Nothing here imports Safwa. Everything is either the shell, the example under
`examples/wallet/`, or the Telegram and provider boundaries replaced — which is also why
`standalone_run.py` can drive the same path in a process where `safwa` cannot be imported
at all.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

REPOSITORY = Path(__file__).resolve().parents[2]
for folder in (REPOSITORY / "examples", REPOSITORY / "tests"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from agent_turns import mutation_turn, route_turn  # noqa: E402
from telegram_fakes import QueueTestMessage, spawn_timer  # noqa: E402
from wallet import app  # noqa: E402
from wallet.wallets.model import CategoryKind  # noqa: E402
from wallet.wallets.use_cases import create_category, create_wallet  # noqa: E402

from llm_gateway import CompletionTurn, ScriptedProvider  # noqa: E402
from telegram_llm import ChatHost, HistoryEntry  # noqa: E402
from tg_agent_shell.ai.sql import ReadOnlyQueryRunner  # noqa: E402
from tg_agent_shell.foundation.database import Database  # noqa: E402
from tg_agent_shell.foundation.kinds import MARKS, MessageKind  # noqa: E402
from tg_agent_shell.history import TelegramNotes  # noqa: E402
from tg_agent_shell.session import RootSession  # noqa: E402
from tg_agent_shell.telegram import Services, callback_token_handler  # noqa: E402
from tg_agent_shell.telegram.dialogue import run_dialogue_turn  # noqa: E402
from tg_agent_shell.telegram.model import CallbackToken  # noqa: E402

OWNER_ID = 42
TIMEZONE = "Europe/Istanbul"
BOT_USER_ID = 999
TODAY = date(2026, 9, 7)
READ = "SELECT id, name FROM ai_wallets WHERE name = 'Cash'"


@dataclass(frozen=True, slots=True)
class Running:
    """One started bot: the container its handlers read, and the script it answers with."""

    sessions: async_sessionmaker[AsyncSession]
    services: Services
    root: RootSession
    provider: ScriptedProvider
    # What the owner sent. Every screen the bot drew is a message of its own beside it.
    message: QueueTestMessage

    @property
    def screen(self) -> QueueTestMessage:
        """The message a button on the newest screen would be pressed on."""
        return self.message.sent[-1] if self.message.sent else self.message

    @property
    def chat(self) -> list[str]:
        """Everything drawn in this chat, in order, wherever it was drawn."""
        return self.message.bot.drawn


class WalletHarness:
    """One database file, started and restarted as many times as a test needs.

    A restart is what the shell's recovery contract is about, so it is a method here
    rather than a second fixture: the same file, opened again by the same root.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.database: Database | None = None

    async def start(self, *turns: CompletionTurn) -> Running:
        await self.stop()
        prepared = await app.open_database(self.path, owner_id=OWNER_ID, timezone=TIMEZONE)
        self.database = prepared.database
        sessions = prepared.database.sessions
        provider = ScriptedProvider(turns)
        history = app.build_history(
            sessions, bot_user_id=BOT_USER_ID, owner_id=OWNER_ID, timezone=TIMEZONE
        )
        runner = ReadOnlyQueryRunner(self.path, app.REGISTRY.allowed_views, timezone=TIMEZONE)
        root = app.build_root_session(
            sessions,
            provider,
            runner,
            history,
            owner_id=OWNER_ID,
            timezone=TIMEZONE,
            model_name="shell-test-model",
        )
        services = app.build_services(
            sessions,
            root,
            history,
            ChatHost(TelegramNotes(sessions), MARKS, spawn=spawn_timer),
            owner_id=OWNER_ID,
        )
        owner_message = QueueTestMessage(owner_id=OWNER_ID, is_bot=False, answer_as_new=True)
        return Running(sessions, services, root, provider, owner_message)

    async def stop(self) -> None:
        if self.database is not None:
            await self.database.dispose()
            self.database = None


async def seed_lists(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """The two lists the owner keeps by hand, which an entry is written against."""
    async with sessions() as session:
        cash = await create_wallet(session, name="Cash", currency="USD")
        card = await create_wallet(session, name="Card", currency="EUR")
        salary = await create_category(session, name="Salary", kind=CategoryKind.INCOME)
        food = await create_category(session, name="Food", kind=CategoryKind.EXPENSE)
        await session.commit()
        return {"cash": cash.id, "card": card.id, "salary": salary.id, "food": food.id}


def query_turn(sql: str) -> CompletionTurn:
    return mutation_turn(("query_data", {"sql": sql}), prefix="read")


def entry_script(ids: dict[str, int], *, answer: str) -> list[CompletionTurn]:
    """One whole turn: the root session routes, the bookkeeper reads and proposes, both close."""
    return [
        route_turn("bookkeeper"),
        query_turn(READ),
        mutation_turn(
            (
                "entry",
                {
                    "mode": "create",
                    "wallet_id": ids["cash"],
                    "category_id": ids["food"],
                    "amount_minor": 1250,
                    "happened_on": TODAY.isoformat(),
                    "note": "Lunch",
                },
            )
        ),
        CompletionTurn(content="Written down."),
        CompletionTurn(content=answer),
    ]


class Press:
    """One inline button, pressed. The token is the only thing a screen hands out."""

    def __init__(self, token: str, message: QueueTestMessage) -> None:
        self.data = f"cb:{token}"
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


async def take_a_turn(running: Running, request: str) -> None:
    source = HistoryEntry(
        message_id=running.message.message_id,
        sender_id=running.services.owner_id,
        role="user",
        text=request,
        created_at=running.message.date.astimezone(UTC),
        kind=MessageKind.DIALOGUE_USER.value,
    )
    await run_dialogue_turn(running.message, running.services, request, source)


async def live_token(running: Running, action: str) -> str:
    async with running.sessions() as session:
        token = await session.scalar(
            select(CallbackToken).where(
                CallbackToken.action == action, CallbackToken.consumed_at.is_(None)
            )
        )
    assert token is not None, f"no live {action} button on the screen"
    return token.token


async def press(running: Running, action: str) -> None:
    await callback_token_handler(
        Press(await live_token(running, action), running.screen), running.services
    )
