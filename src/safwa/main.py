from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import suppress
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .ai.autoapproval import AutoApprovalReviewer
from .ai.board import BOARD_PROMPT, BOARD_TOOLS
from .ai.diary import DIARY_PROMPT, day_read_tool, diary_clock
from .ai.provider import OpenAICompatibleProvider, ProviderConfig
from .ai.service import AIAdvisor, query_read_tool
from .ai.sql import ReadOnlyQueryRunner, create_ai_views
from .ai.subagents import RoutedSubagent
from .asr import build_transcriber
from .config import Settings
from .constants import AI_APP_TITLE, AI_APP_URL
from .continuity import PersonaContinuity, run_memory_maintenance
from .db import Database, upgrade_database
from .domain import bootstrap_workspace
from .enums import AIProvider, MessageKind
from .history import TelegramHistorySource, mark_message, register_message
from .memory import MemoryFileStore
from .models import Workspace
from .recovery import recover_startup
from .scheduler import run_scheduler, run_sprint_expiry
from .telegram import (
    BACKGROUND_SOURCE_ID,
    GenerationGuard,
    OwnerAndWritingMiddleware,
    ReminderRuntime,
    Services,
    router,
    sync_bot_commands,
)

logger = logging.getLogger(__name__)
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format=_LOG_FORMAT, force=True)

    # Keep Safwa's own request/response and error logs visible even if a
    # dependency reconfigures the root logger after startup.
    safwa_logger = logging.getLogger("safwa")
    safwa_logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    safwa_logger.addHandler(handler)
    safwa_logger.setLevel(level)
    safwa_logger.propagate = False
    safwa_logger.disabled = False


def database_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Safwa v1 requires a local SQLite database")
    return Path(database_url.removeprefix("sqlite:///"))


async def run(settings: Settings) -> None:
    configure_logging(settings.log_level)
    logger.info("Safwa console logging enabled (level=%s)", settings.log_level.upper())
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

    headers: tuple[tuple[str, str], ...] = ()
    if settings.ai_provider is AIProvider.OPENROUTER:
        headers = (("HTTP-Referer", AI_APP_URL), ("X-Title", AI_APP_TITLE))
    provider = OpenAICompatibleProvider(
        ProviderConfig(
            base_url=settings.resolved_ai_base_url,
            api_key=settings.ai_api_key.get_secret_value(),
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_output_tokens=settings.ai_max_output_tokens,
            structured_output=settings.ai_structured_output,
            tool_choice_required=settings.ai_tool_choice_required,
            max_retries=settings.resolved_ai_max_retries,
            send_temperature=settings.resolved_ai_send_temperature,
            reasoning_effort=settings.ai_reasoning_effort,
            default_headers=headers,
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
    query_runner = ReadOnlyQueryRunner(
        database_path(settings.database_url),
        row_limit=settings.ai_query_row_limit,
        char_budget=settings.ai_query_char_budget,
    )
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        # Item citations are t.me links to this bot; a preview card under every answer
        # would be noise.
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    me = await bot.get_me()
    history = TelegramHistorySource.from_settings(settings, database.sessions, bot_user_id=me.id)
    await history.start()
    # The advisor is built after the history source because a subagent reads through it.
    advisor = AIAdvisor(
        database.sessions,
        provider,
        memory,
        query_runner,
        model_name=settings.ai_model,
        provider_name=settings.ai_provider.value,
        cache_breakpoints=settings.resolved_ai_cache_breakpoints,
        autoapproval=AutoApprovalReviewer(provider),
        subagents=(
            RoutedSubagent(
                name="board",
                purpose="every change to a Card, Check, Value, Tag, Request or Reminder",
                instructions=BOARD_PROMPT,
                read_tools=(query_read_tool(query_runner),),
                mutation_tools=BOARD_TOOLS,
                planning_state=True,
            ),
            RoutedSubagent(
                name="diary",
                purpose=(
                    "the Diary — reading a day, writing one, rewriting one, removing one"
                ),
                instructions=DIARY_PROMPT,
                read_tools=(
                    day_read_tool(
                        history,
                        chat_id=settings.telegram_owner_id,
                        timezone=settings.timezone,
                    ),
                    query_read_tool(query_runner),
                ),
                mutation_tools=("diary",),
                # Enough to be told what to change about the day it just proposed; the day
                # itself it reads with `read_day`.
                clock=lambda: diary_clock(settings.timezone),
            ),
        ),
    )
    continuity = PersonaContinuity(
        database.sessions,
        history,
        provider,
        memory,
        summary_trigger_tokens=settings.summary_trigger_tokens,
        chars_per_token=settings.token_chars_estimate,
    )
    guard = GenerationGuard()
    transcriber = build_transcriber(settings)
    if transcriber is not None:
        logger.info(
            "Voice input enabled: %s %s (language=%s)",
            settings.asr_provider.value,
            settings.resolved_asr_model,
            settings.asr_language or "auto",
        )
    services = Services(
        sessions=database.sessions,
        advisor=advisor,
        history=history,
        memory=memory,
        continuity=continuity,
        owner_id=settings.telegram_owner_id,
        guard=guard,
        bot_username=settings.telegram_bot_username,
        transcriber=transcriber,
    )
    dispatcher = Dispatcher()
    router.message.outer_middleware.register(OwnerAndWritingMiddleware())
    router.callback_query.outer_middleware.register(OwnerAndWritingMiddleware())
    dispatcher.include_router(router)
    dispatcher["services"] = services
    async with database.sessions() as session:
        workspace = await session.get(Workspace, 1)
        sprint_active = bool(workspace and workspace.active_sprint_id)
    await sync_bot_commands(bot, sprint_active=sprint_active)

    async def memory_error(text: str) -> None:
        marked_text, event_id = mark_message(f"⚠️ memory.md: {text}", MessageKind.ERROR)
        sent = await bot.send_message(
            settings.telegram_owner_id,
            marked_text,
        )
        async with database.sessions() as session:
            await register_message(
                session,
                sent.chat.id,
                sent.message_id,
                "out",
                MessageKind.ERROR,
                event_id=event_id,
            )
            await session.commit()

    memory_task = asyncio.create_task(memory.poll(memory_error), name="memory-file-poll")
    reminders = ReminderRuntime(
        services, bot, owner_id=settings.telegram_owner_id, timezone=settings.timezone
    )
    scheduler_task = None
    if settings.scheduler_enabled:
        scheduler_task = asyncio.create_task(
            run_scheduler(
                database.sessions,
                timezone=settings.timezone,
                gate=reminders.can_escalate,
                still_current=reminders.still_current,
                release=reminders.release,
                escalate=reminders.escalate,
                poll_seconds=settings.scheduler_poll_seconds,
            ),
            name="reminder-scheduler",
        )
    async def announce_sprint_expiry(number: int) -> None:
        text = (
            f"⏹ Sprint {number} reached its planned end date and was closed automatically. "
            "Whatever was still open kept its stage."
        )
        marked_text, event_id = mark_message(text, MessageKind.RECEIPT)
        sent = await bot.send_message(settings.telegram_owner_id, marked_text)
        async with database.sessions() as session:
            await register_message(
                session,
                sent.chat.id,
                sent.message_id,
                "out",
                MessageKind.RECEIPT,
                event_id=event_id,
            )
            await session.commit()
        await sync_bot_commands(bot, sprint_active=False)

    sprint_expiry_task = asyncio.create_task(
        run_sprint_expiry(database.sessions, announce=announce_sprint_expiry),
        name="sprint-expiry",
    )
    memory_maintenance_task = asyncio.create_task(
        run_memory_maintenance(
            continuity,
            database.sessions,
            settings.telegram_owner_id,
            lambda: guard.active,
            settings.timezone,
            reserve_background=guard.reserve_background,
            dialogue_revision=lambda: guard.dialogue_revision,
            release_background=lambda: guard.release(BACKGROUND_SOURCE_ID),
        ),
        name="memory-maintenance",
    )
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        for task in (memory_task, scheduler_task, sprint_expiry_task, memory_maintenance_task):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await history.close()
        if transcriber is not None:
            await transcriber.close()
        await provider.close()
        await bot.session.close()
        await database.dispose()


def main() -> None:
    settings = Settings()
    upgrade_database(settings.database_url)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
