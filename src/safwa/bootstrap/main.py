from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Coroutine
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import OpenAICompatibleConfig, OpenAICompatibleProvider
from telegram_llm import ChatHost

from ..adapters.asr import build_transcriber
from ..adapters.kinds import MARKS
from ..adapters.telegram_history import TelegramHistorySource, TelegramNotes
from ..ai.autoapproval import AutoApprovalReviewer
from ..ai.sql import ReadOnlyQueryRunner, create_ai_views
from ..config import Settings
from ..constants import AI_APP_TITLE, AI_APP_URL
from ..enums import AIProvider
from ..features.advisor.session import AIAdvisor
from ..features.continuity.memory import MemoryFileStore
from ..features.continuity.persona import PersonaContinuity
from ..features.heavy_analyzer import agent as heavy_analyzer
from ..features.profile.model import UserProfile
from ..foundation.database import Database, upgrade_database
from ..foundation.errors import DomainError
from ..foundation.workspace import Workspace
from ..recovery import recover_startup
from ..shell import (
    SHELL_COMMANDS,
    OwnerAndWritingMiddleware,
    Services,
    discard_stale_status,
    register_commands,
    router,
    sync_bot_commands,
)
from ..shell.manifest import AgentContext, BackgroundContext
from ..turn import TurnManager
from ..turn import dialogue as _dialogue  # noqa: F401  registers the owner-message handlers
from .auth import history_client
from .modules import (
    AI_VIEWS,
    ALLOWED_VIEWS,
    BACKGROUND_TASKS,
    FEATURE_CALLBACK_ACTIONS,
    FEATURE_COMMANDS,
    FEATURE_START_LINKS,
    FEATURE_TEXT_INPUTS,
    HEAVY_ANALYZER_PROMPT,
    PROPOSALS,
    RECOVERY_HOOKS,
    SCREENS,
    SYSTEM_PROMPT,
    routed_subagents,
)

logger = logging.getLogger(__name__)
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    # A Windows console is cp1251 here, and every stage name, receipt and prompt Safwa logs
    # carries emoji.  Without this the handler raises UnicodeEncodeError per line and prints
    # a logging traceback instead of the record.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
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


async def bootstrap_workspace(session: AsyncSession, owner_id: int, timezone: str) -> Workspace:
    """Bind the database to its owner, and seed the two rows every screen assumes.

    The Profile is a feature's model and the Workspace is not, so neither of them could
    seed the other; the composition root is where both are in reach.
    """
    workspace = await session.get(Workspace, 1)
    if workspace is None:
        workspace = Workspace(id=1, owner_telegram_id=owner_id, timezone=timezone)
        session.add(workspace)
    elif workspace.owner_telegram_id != owner_id:
        raise DomainError("The database is already bound to another Telegram owner")
    if await session.get(UserProfile, 1) is None:
        session.add(UserProfile(id=1))
    await session.flush()
    return workspace


def database_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Safwa v1 requires a local SQLite database")
    return Path(database_url.removeprefix("sqlite:///"))


def _report_background_exit(task: asyncio.Task[None]) -> None:
    """A background loop that ends before shutdown has stopped its feature for good.

    Nothing awaits these tasks while polling runs, so an exception inside one is
    swallowed by asyncio and the feature simply stops working until the next restart.
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("Background task %s stopped", task.get_name(), exc_info=error)
    else:
        logger.warning("Background task %s returned before shutdown", task.get_name())


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
        await recover_startup(session, RECOVERY_HOOKS)
        await session.run_sync(
            lambda sync_session: create_ai_views(sync_session.connection(), AI_VIEWS)
        )
        await session.commit()

    headers: tuple[tuple[str, str], ...] = ()
    if settings.ai_provider is AIProvider.OPENROUTER:
        headers = (("HTTP-Referer", AI_APP_URL), ("X-Title", AI_APP_TITLE))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
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
        ALLOWED_VIEWS,
        row_limit=settings.ai_query_row_limit,
        char_budget=settings.ai_query_char_budget,
        timezone=settings.timezone,
    )
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        # Item citations are t.me links to this bot; a preview card under every answer
        # would be noise.
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    me = await bot.get_me()
    history = TelegramHistorySource(
        history_client(settings) if settings.telegram_history_enabled else None,
        database.sessions,
        marks=MARKS,
        bot_user_id=me.id,
        owner_id=settings.telegram_owner_id,
        citation_types=SCREENS.types,
        timezone=settings.timezone,
    )
    await history.start()
    # The advisor is built after the history source because a subagent reads through it.
    advisor = AIAdvisor(
        database.sessions,
        provider,
        memory,
        query_runner,
        PROPOSALS,
        screens=SCREENS,
        system_prompt=SYSTEM_PROMPT,
        model_name=settings.ai_model,
        provider_name=settings.ai_provider.value,
        cache_breakpoints=settings.resolved_ai_cache_breakpoints,
        autoapproval=AutoApprovalReviewer(provider),
        subagents=routed_subagents(
            AgentContext(
                owner_id=settings.telegram_owner_id,
                timezone=settings.timezone,
                query_runner=query_runner,
                history=history,
            )
        ),
        helpers={
            heavy_analyzer.NAME: heavy_analyzer.build(
                provider, query_runner, prompt=HEAVY_ANALYZER_PROMPT
            )
        },
    )
    continuity = PersonaContinuity(
        database.sessions,
        history,
        provider,
        memory,
        summary_trigger_tokens=settings.summary_trigger_tokens,
        chars_per_token=settings.token_chars_estimate,
    )
    turn = TurnManager()
    timers: set[asyncio.Task[None]] = set()

    def spawn(work: Coroutine[None, None, None], name: str) -> asyncio.Task[None]:
        """A Toast timer, held so that shutdown ends it rather than leaving it running."""
        timer = asyncio.create_task(work, name=name)
        timers.add(timer)
        timer.add_done_callback(timers.discard)
        return timer

    chat = ChatHost(TelegramNotes(database.sessions), MARKS, spawn=spawn)
    # Home is a feature now, and `/start` leads the published list, so its commands come first.
    commands = (*FEATURE_COMMANDS, *SHELL_COMMANDS)
    register_commands(router, commands)
    transcriber = build_transcriber(
        provider=settings.asr_provider,
        model=settings.resolved_asr_model,
        base_url=settings.resolved_asr_base_url,
        api_key=settings.asr_api_key.get_secret_value(),
        language=settings.asr_language,
        device=settings.asr_device,
        compute_type=settings.asr_compute_type,
        log_timing=settings.asr_log_timing,
    )
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
        turn=turn,
        chat=chat,
        screens=SCREENS,
        commands=commands,
        callback_actions=FEATURE_CALLBACK_ACTIONS,
        text_inputs=FEATURE_TEXT_INPUTS,
        start_links=FEATURE_START_LINKS,
        views=ALLOWED_VIEWS,
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
    await sync_bot_commands(bot, commands, sprint_active=sprint_active)
    await discard_stale_status(bot, services, settings.telegram_owner_id)

    background = BackgroundContext(
        owner_id=settings.telegram_owner_id,
        timezone=settings.timezone,
        scheduler_enabled=settings.scheduler_enabled,
        poll_seconds=settings.scheduler_poll_seconds,
        sessions=database.sessions,
        bot=bot,
        services=services,
    )
    tasks = [
        asyncio.create_task(task.run(background), name=task.name) for task in BACKGROUND_TASKS
    ]
    for task in tasks:
        task.add_done_callback(_report_background_exit)
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        live_timers = tuple(timers)
        for timer in live_timers:
            timer.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*live_timers, *tasks, return_exceptions=True)
        await history.close()
        if transcriber is not None:
            await transcriber.close()
        await provider.aclose()
        await bot.session.close()
        await database.dispose()


def main() -> None:
    settings = Settings()
    upgrade_database(settings.database_url)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
