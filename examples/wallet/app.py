"""The composition root: what this application is, put together once.

Everything derived from the feature list is the shell's `Registry`; what is written here is
only what the shell cannot know — which features exist, what a world is, what the model is
told it is, and where the durable notes come from.

Run it: `BOT_TOKEN=... OWNER_ID=... uv run python -m wallet.app` from `examples/`.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_gateway import LlmProvider, OpenAICompatibleConfig, OpenAICompatibleProvider
from telegram_llm import ChatHost
from tg_agent_shell.ai.messages import StateBlocks
from tg_agent_shell.ai.sql import ReadOnlyQueryRunner, create_ai_views, view_catalogue
from tg_agent_shell.foundation.database import Database, upgrade_database
from tg_agent_shell.foundation.kinds import MARKS
from tg_agent_shell.history import TelegramHistorySource, TelegramNotes
from tg_agent_shell.proposals.module import MODULE as PROPOSALS_FEATURE
from tg_agent_shell.recovery import recover_startup
from tg_agent_shell.registry import Registry
from tg_agent_shell.session import RootSession
from tg_agent_shell.telegram import SHELL_COMMANDS, Services, sync_bot_commands
from tg_agent_shell.telegram.manifest import AgentContext, FeatureModule
from tg_agent_shell.telegram.routing import build_router
from tg_agent_shell.turn import TurnManager

from .ledger.module import MODULE as LEDGER
from .models import Base
from .wallets.model import Wallet
from .wallets.module import MODULE as WALLETS
from .wallets.use_cases import wallet_balance
from .world import bootstrap_ledger, ledger_world

MODULES: tuple[FeatureModule, ...] = (WALLETS, LEDGER, PROPOSALS_FEATURE)

REGISTRY: Registry = Registry.of(MODULES, world=ledger_world)

PERSONA = "You keep one person's money ledger. Be brief and concrete."

# The Advisor's own list, kept explicit: a view it is not told about is a view it is
# refused, whatever the ledger publishes.
ROOT_VIEWS = ("ai_wallets", "ai_categories", "ai_wallet_balances")

SYSTEM_PROMPT = f"""{PERSONA}

You answer in words. You never write to the ledger yourself: hand the turn over instead.

{REGISTRY.routes(lambda agent: f'- `route("{agent.name}")` — {agent.purpose}')}

`query_data` runs one read-only SELECT over these views only:
{view_catalogue(REGISTRY.views, ROOT_VIEWS)}
"""


class NoNotes:
    """This application keeps no durable notes, and says so once rather than every turn."""

    text = ""

    async def sync(self) -> NoNotes:
        return self


class NoEdge:
    """Nothing but the token budget ends this application's window."""

    def ends_window(self, message_id: int, kind: str | None, text: str) -> bool:
        return False

    def stands_for(self, parts: Sequence[str]) -> str:
        return ""


def estimate_tokens(text: str) -> int:
    return max(len(text) // 4, 1)


def world_state(timezone: str):
    """What the root session reads before its own steps: the wallets, then the clock."""

    async def blocks(session: AsyncSession) -> StateBlocks:
        wallets = list(await session.scalars(select(Wallet).order_by(Wallet.name)))
        lines = [
            f"- [{wallet.name}](wallet:{wallet.id}) "
            f"{await wallet_balance(session, wallet.id)} minor {wallet.currency}"
            for wallet in wallets
        ]
        now = datetime.now(ZoneInfo(timezone))
        return StateBlocks(
            state="Wallets:\n" + ("\n".join(lines) if lines else "- none yet"),
            clock=f"Current local time: {now:%Y-%m-%d %H:%M} ({timezone})",
        )

    return blocks


@dataclass(frozen=True, slots=True)
class Bootstrapped:
    """What one prepared database hands back to whoever starts the application."""

    database: Database
    path: Path


async def open_database(path: Path, *, owner_id: int, timezone: str) -> Bootstrapped:
    """Bring the schema up, bind the world row, rebuild the views, reconcile a restart."""
    upgrade_database(f"sqlite:///{path.as_posix()}", Base.metadata)
    database = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    async with database.sessions() as session:
        await bootstrap_ledger(session, owner_id, timezone)
        await recover_startup(session, REGISTRY.recovery)
        await session.run_sync(
            lambda sync_session: create_ai_views(sync_session.connection(), REGISTRY.views)
        )
        await session.commit()
    return Bootstrapped(database, path)


def build_root_session(
    sessions: async_sessionmaker[AsyncSession],
    provider: LlmProvider,
    query_runner: ReadOnlyQueryRunner,
    history: TelegramHistorySource,
    *,
    owner_id: int,
    timezone: str,
    model_name: str,
) -> RootSession:
    return REGISTRY.root_session(
        sessions,
        provider,
        NoNotes(),
        query_runner,
        views=ROOT_VIEWS,
        workspace_state=world_state(timezone),
        system_prompt=SYSTEM_PROMPT,
        model_name=model_name,
        subagents=REGISTRY.subagents(
            AgentContext(
                owner_id=owner_id,
                timezone=timezone,
                query_runner=query_runner,
                history=history,
            ),
            prompt=lambda agent: f"{PERSONA}\n{agent.instructions}",
        ),
    )


def build_services(
    sessions: async_sessionmaker[AsyncSession],
    root: RootSession,
    history: TelegramHistorySource,
    chat: ChatHost,
    *,
    owner_id: int,
) -> Services:
    return Services(
        sessions=sessions,
        root=root,
        history=history,
        owner_id=owner_id,
        turn=TurnManager(),
        chat=chat,
        screens=REGISTRY.screens,
        commands=(*REGISTRY.commands, *SHELL_COMMANDS),
        callback_actions=REGISTRY.callback_actions,
        text_inputs=REGISTRY.text_inputs,
        hooks=REGISTRY.hooks,
        start_links=REGISTRY.start_links,
        views=REGISTRY.allowed_views,
    )


def build_history(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_user_id: int,
    owner_id: int,
    timezone: str,
) -> TelegramHistorySource:
    """No Telethon here: the window is read out of the notes this bot wrote itself."""
    return TelegramHistorySource(
        None,
        sessions,
        marks=MARKS,
        bot_user_id=bot_user_id,
        owner_id=owner_id,
        count_tokens=estimate_tokens,
        token_budget=8_000,
        edge=NoEdge(),
        citation_types=REGISTRY.screens.types,
        timezone=timezone,
    )


async def main() -> None:
    owner_id = int(os.environ["OWNER_ID"])
    timezone = os.environ.get("TIMEZONE", "UTC")
    prepared = await open_database(
        Path(os.environ.get("WALLET_DB", "wallet.db")), owner_id=owner_id, timezone=timezone
    )
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url=os.environ.get("LLM_URL", "http://localhost:1234/v1"),
            api_key=os.environ.get("LLM_KEY", "not-needed"),
            model=os.environ.get("LLM_MODEL", "local-model"),
        )
    )
    bot = Bot(
        token=os.environ["BOT_TOKEN"],
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    me = await bot.get_me()
    timers: set[asyncio.Task[None]] = set()

    def spawn(work: Coroutine[None, None, None], name: str) -> asyncio.Task[None]:
        timer = asyncio.create_task(work, name=name)
        timers.add(timer)
        timer.add_done_callback(timers.discard)
        return timer

    history = build_history(
        prepared.database.sessions, bot_user_id=me.id, owner_id=owner_id, timezone=timezone
    )
    await history.start()
    query_runner = ReadOnlyQueryRunner(
        prepared.path, REGISTRY.allowed_views, timezone=timezone
    )
    root = build_root_session(
        prepared.database.sessions,
        provider,
        query_runner,
        history,
        owner_id=owner_id,
        timezone=timezone,
        model_name=os.environ.get("LLM_MODEL", "local-model"),
    )
    services = build_services(
        prepared.database.sessions,
        root,
        history,
        ChatHost(TelegramNotes(prepared.database.sessions), MARKS, spawn=spawn),
        owner_id=owner_id,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(services.commands))
    dispatcher["services"] = services
    await sync_bot_commands(bot, services.commands)
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        for timer in tuple(timers):
            timer.cancel()
        await asyncio.gather(*timers, return_exceptions=True)
        await history.close()
        await provider.aclose()
        await bot.session.close()
        await prepared.database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
