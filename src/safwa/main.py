from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .ai.provider import OpenAICompatibleProvider, ProviderConfig
from .ai.service import AIAdvisor
from .ai.sql import ReadOnlyQueryRunner, create_ai_views
from .config import Settings
from .continuity import PersonaContinuity, run_memory_maintenance
from .db import Database, upgrade_database
from .domain import bootstrap_workspace
from .enums import MessageKind
from .history import TelegramHistorySource, register_message
from .memory import MemoryFileStore
from .recovery import recover_startup
from .scheduler import ReminderPolicy, run_scheduler
from .telegram import GenerationGuard, OwnerAndWritingMiddleware, Services, router


def database_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Safwa v1 requires a local SQLite database")
    return Path(database_url.removeprefix("sqlite:///"))


async def run(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    if settings.telegram_history_required and not settings.telegram_history_enabled:
        raise RuntimeError(
            "Canonical Telegram history is required. Set SAFWA_TELEGRAM_API_ID and "
            "SAFWA_TELEGRAM_API_HASH, then run `uv run safwa-auth`."
        )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(settings.async_database_url)
    async with database.sessions() as session:
        await bootstrap_workspace(session, settings.telegram_owner_id, settings.timezone)
        await recover_startup(session)
        await session.run_sync(lambda sync_session: create_ai_views(sync_session.connection()))
        await session.commit()

    provider = OpenAICompatibleProvider(
        ProviderConfig(
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key.get_secret_value(),
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_output_tokens=settings.ai_max_output_tokens,
            structured_output=settings.ai_structured_output,
        )
    )
    memory = MemoryFileStore(
        settings.memory_path,
        database.sessions,
        token_budget=settings.memory_token_budget,
        chars_per_token=settings.token_chars_estimate,
        poll_seconds=settings.memory_poll_seconds,
    )
    await memory.sync()
    query_runner = ReadOnlyQueryRunner(database_path(settings.database_url))
    advisor = AIAdvisor(
        database.sessions,
        provider,
        memory,
        query_runner,
        model_name=settings.ai_model,
    )
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    history = TelegramHistorySource.from_settings(settings, database.sessions, bot_user_id=me.id)
    await history.start()
    continuity = PersonaContinuity(
        database.sessions,
        history,
        provider,
        memory,
        summary_trigger_tokens=settings.summary_trigger_tokens,
        chars_per_token=settings.token_chars_estimate,
    )
    guard = GenerationGuard()
    services = Services(
        sessions=database.sessions,
        advisor=advisor,
        history=history,
        memory=memory,
        continuity=continuity,
        owner_id=settings.telegram_owner_id,
        guard=guard,
    )
    dispatcher = Dispatcher()
    router.message.outer_middleware.register(OwnerAndWritingMiddleware())
    router.callback_query.outer_middleware.register(OwnerAndWritingMiddleware())
    dispatcher.include_router(router)
    dispatcher["services"] = services
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Open Safwa"),
            BotCommand(command="today", description="Today dashboard"),
            BotCommand(command="sprint", description="Planning or Sprint"),
            BotCommand(command="backlog", description="Backlog dashboard"),
            BotCommand(command="add", description="Create a card draft"),
            BotCommand(command="drafts", description="Pending drafts"),
            BotCommand(command="values", description="Values in focus"),
            BotCommand(command="newvalue", description="Create a Value"),
            BotCommand(command="newboard", description="Create a Board"),
            BotCommand(command="retro", description="Latest retrospective"),
            BotCommand(command="feedback", description="Pending completion feedback"),
            BotCommand(command="settings", description="Profile and reminders"),
            BotCommand(command="setwake", description="Set wake time HH:MM"),
            BotCommand(command="setbed", description="Set bed time HH:MM"),
            BotCommand(command="setquiet", description="Set quiet range HH:MM-HH:MM"),
            BotCommand(command="setcapacity", description="Set Sprint capacity"),
            BotCommand(command="memory", description="Inspect memory.md"),
            BotCommand(command="status", description="Safwa diagnostics"),
            BotCommand(command="cancel", description="Cancel generation"),
        ]
    )

    async def memory_error(text: str) -> None:
        sent = await bot.send_message(settings.telegram_owner_id, f"⚠️ memory.md: {text}")
        async with database.sessions() as session:
            await register_message(
                session,
                sent.chat.id,
                sent.message_id,
                "out",
                MessageKind.ERROR,
            )
            await session.commit()

    async def send_reminder(text: str) -> bool:
        if guard.active:
            return False
        snapshot = await memory.sync()
        raw = await provider.complete(
            [
                {
                    "role": "system",
                    "content": "You are Safwa. Given one deterministically eligible reminder candidate, "
                    "either compose one warm concise advisor message or send nothing when it would not help. "
                    'Return JSON only: {"send":true|false,"message":"..."}.',
                },
                {
                    "role": "user",
                    "content": f"Current UTC time: {datetime.now(UTC).isoformat()}\n"
                    f"Candidate: {text}\nPersistent memory:\n{snapshot.text}",
                },
            ],
            temperature=0.2,
        )
        if guard.active:
            return False
        try:
            decision = json.loads(raw.removeprefix("```json").removesuffix("```").strip())
            if not decision.get("send") or not str(decision.get("message", "")).strip():
                return False
            reminder_text = str(decision["message"]).strip()
        except (json.JSONDecodeError, AttributeError, TypeError):
            return False
        sent = await bot.send_message(settings.telegram_owner_id, reminder_text)
        async with database.sessions() as session:
            await register_message(
                session,
                sent.chat.id,
                sent.message_id,
                "out",
                MessageKind.REMINDER,
            )
            await session.commit()
        return True

    memory_task = asyncio.create_task(memory.poll(memory_error), name="memory-file-poll")
    scheduler_task = asyncio.create_task(
        run_scheduler(
            database.sessions,
            ReminderPolicy(settings.timezone),
            send_reminder,
            poll_seconds=settings.scheduler_poll_seconds,
        ),
        name="reminder-scheduler",
    )
    memory_maintenance_task = asyncio.create_task(
        run_memory_maintenance(
            continuity,
            settings.telegram_owner_id,
            lambda: guard.active,
        ),
        name="memory-maintenance",
    )
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        for task in (memory_task, scheduler_task, memory_maintenance_task):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await history.close()
        await provider.close()
        await bot.session.close()
        await database.dispose()


def main() -> None:
    settings = Settings()
    upgrade_database(settings.database_url)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
