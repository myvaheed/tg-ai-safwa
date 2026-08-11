from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import F
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, func, select

from ..analytics import render_retrospective_png, retrospective_data, retrospective_recommendations
from ..constants import REQUEST_RESULT_LIMIT
from ..continuity import MemoryMaintenanceResult, parse_memory_update_time, record_memory_run
from ..domain import (
    DomainError,
    StaleStateError,
    snooze_reminders,
    sprint_metrics,
    update_profile,
)
from ..enums import CardKind, CardStage, MessageKind
from ..history import register_message
from ..memory import MemoryFileError
from ..models import (
    Card,
    FeedbackQueue,
    SavedRequest,
    Sprint,
    Tag,
    UiSession,
    UserProfile,
    Value,
    Workspace,
)
from ..saved_requests import request_cards
from ._core import Services, router
from ._messaging import (
    delete_message_range,
    send_registered,
    send_subsession_result,
    token_button,
)
from ._presentation import (
    kind_label,
    menu_markup,
    menu_row,
    retro_back_row,
    with_notice,
)
from .cards import render_dashboard, start_manual_card_creation

logger = logging.getLogger(__name__)


async def end_subsession(
    message: Message,
    services: Services,
    *,
    start_message_id: int,
    instruction: str,
) -> None:
    active_start = await services.history.active_session_start(message.chat.id)
    if active_start is None or active_start.message_id != start_message_id:
        raise StaleStateError("This subsession has changed or is no longer active")
    await services.guard.acquire(message.message_id)
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        transcript = await services.history.dialogue(message.chat.id)
        result = await services.advisor.compress_subsession(transcript, instruction)
        await delete_message_range(message, start_message_id, message.message_id)
        await send_subsession_result(message, services, active_start.text, result)
    finally:
        services.guard.release(message.message_id)


@router.message(Command("start"))
async def command_start(message: Message, services: Services) -> None:
    await send_registered(
        message,
        services,
        "<b>Safwa</b>\nYour personal agile advisor. Choose a dashboard or just write to me.",
        kind=MessageKind.DASHBOARD,
        markup=menu_markup(),
    )


@router.message(Command("newsession"))
async def command_newsession(message: Message, services: Services) -> None:
    parts = (message.text or "").split(maxsplit=1)
    initial_request = parts[1].strip() if len(parts) == 2 else ""
    if not initial_request:
        await send_registered(
            message,
            services,
            "Usage: <code>/newsession your initial request or situation</code>",
            kind=MessageKind.ERROR,
        )
        return
    async with services.sessions() as session:
        await register_message(
            session,
            message.chat.id,
            message.message_id,
            "in",
            MessageKind.SESSION_START,
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "🆕 New Safwa session started. I will use this initial request as the dialogue boundary.",
        kind=MessageKind.RECEIPT,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


@router.message(Command("endsession"))
async def command_endsession(message: Message, services: Services) -> None:
    active_start = await services.history.active_session_start(message.chat.id)
    if active_start is None:
        await send_registered(
            message,
            services,
            "There is no active <code>/newsession</code> branch to end.",
            kind=MessageKind.ERROR,
            markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
        )
        return
    parts = (message.text or "").split(maxsplit=1)
    instruction = parts[1].strip() if len(parts) == 2 else ""
    async with services.sessions() as session:
        confirm = await token_button(
            session,
            services.owner_id,
            "✅ Compress and delete branch",
            "subsession_confirm",
            {
                "start_message_id": active_start.message_id,
                "instruction": instruction,
            },
        )
        cancel = await token_button(
            session,
            services.owner_id,
            "↩️ Keep branch",
            "subsession_cancel",
        )
        await session.commit()
    instruction_text = (
        html.escape(instruction) if instruction else "Use a concise planning/advisory result."
    )
    await send_registered(
        message,
        services,
        "<b>End this subsession?</b>\n"
        "Safwa will compress everything since the active <code>/newsession</code>, delete that "
        "branch from Telegram, and leave one compact context result.\n\n"
        f"<b>Initial request:</b>\n<blockquote>{html.escape(active_start.text)}</blockquote>\n\n"
        f"<b>Result instruction:</b>\n<blockquote>{instruction_text}</blockquote>",
        kind=MessageKind.APPROVAL,
        markup=InlineKeyboardMarkup(inline_keyboard=[[confirm], [cancel]]),
    )


@router.message(Command("today"))
async def command_today(message: Message, services: Services) -> None:
    await render_dashboard(message, services, CardStage.TODAY, title="Today")


@router.message(Command("backlog"))
async def command_backlog(message: Message, services: Services) -> None:
    await render_dashboard(message, services, CardStage.BACKLOG, title="Backlog")


@router.message(Command("sprint"))
async def command_sprint(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        if workspace and workspace.active_sprint_id:
            sprint = await session.get(Sprint, workspace.active_sprint_id)
            metrics = await sprint_metrics(session, workspace.active_sprint_id)
            start = await token_button(
                session, services.owner_id, "⏹ Finish early", "sprint_finish"
            )
            text = (
                f"<b>Sprint {sprint.number}</b> · {sprint.planned_start_date}–{sprint.planned_end_date}\n"
                f"Committed {metrics['committed']} · Added {metrics['added']} · "
                f"Done {metrics['completed']} · Cancelled {metrics['cancelled']}"
            )
        else:
            selected_effort = (
                await session.scalar(
                    select(func.coalesce(func.sum(Card.effort_points), 0)).where(
                        Card.kind == CardKind.ACTION.value,
                        Card.archived_at.is_(None),
                        Card.effective_stage.in_(
                            [
                                CardStage.SPRINT.value,
                                CardStage.TODAY.value,
                            ]
                        ),
                    )
                )
                or 0
            )
            profile = await session.get(UserProfile, 1)
            start = await token_button(
                session, services.owner_id, "▶️ Start 14-day Sprint", "sprint_start"
            )
            warning = ""
            if profile.capacity_effort_points and selected_effort > profile.capacity_effort_points:
                warning = f"\n⚠️ Above configured capacity ({profile.capacity_effort_points} EP)."
            text = (
                "<b>Planning</b>\nCards in Sprint and Today are preselected for the next Sprint."
                f"\nSelected effort: {selected_effort} EP{warning}"
            )
        await session.commit()
    await send_registered(
        message,
        services,
        text,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [start],
                menu_row(),
            ]
        ),
    )


async def command_add(message: Message, services: Services) -> None:
    await start_manual_card_creation(message, services)


@router.message(Command("advisor"))
async def command_advisor(message: Message, services: Services) -> None:
    await send_registered(
        message,
        services,
        "<b>Advisor</b>\nWrite naturally and Safwa can answer, analyze your planning, or prepare a "
        "reviewable change. Use <code>/newsession</code> for a focused branch.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


@router.message(Command("values"))
async def command_values(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        values = list(
            await session.scalars(
                select(Value).where(Value.archived_at.is_(None)).order_by(Value.name)
            )
        )
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"{'✅' if value.active else '○'} {value.name}",
                    "item_view",
                    {"entity": "value", "id": value.id},
                )
            ]
            for value in values
        ]
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "➕ Add Value", "value_create_prompt", {}
                )
            ]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Values in focus</b>\nActive Values are injected into the advisor context.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


@router.message(Command("tags"))
async def command_tags(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        tags = list(
            await session.scalars(select(Tag).where(Tag.archived_at.is_(None)).order_by(Tag.name))
        )
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    tag.name,
                    "item_view",
                    {"entity": "tag", "id": tag.id},
                )
            ]
            for tag in tags
        ]
        rows.append(
            [await token_button(session, services.owner_id, "➕ Add Tag", "tag_create_prompt", {})]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Tags</b>\nUse Tags to group Cards independently of Values.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


@router.message(Command("requests"))
async def command_requests(message: Message, services: Services) -> None:
    """Show AI-authored saved queries; creation intentionally remains advisor-only."""
    async with services.sessions() as session:
        requests = list(
            await session.scalars(
                select(SavedRequest)
                .where(SavedRequest.archived_at.is_(None))
                .order_by(SavedRequest.name)
            )
        )
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    request.name,
                    "request_view",
                    {"id": request.id},
                )
            ]
            for request in requests
        ]
        await session.commit()
    await send_registered(
        message,
        services,
        "<b>Requests</b>\nSaved card queries created by your advisor.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows + [menu_row()]),
    )


async def render_saved_request(message: Message, services: Services, request_id: int) -> None:
    async with services.sessions() as session:
        request = await session.get(SavedRequest, request_id)
        if request is None or request.archived_at is not None:
            raise DomainError("Request no longer exists")
        matches = await request_cards(session, request.query_sql)
        cards = matches[:REQUEST_RESULT_LIMIT]
        rows = [
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"{kind_label(card.kind)} · {card.title}"[:60],
                    "card_view",
                    {"id": card.id, "back": {"kind": "request", "id": request.id}},
                )
            ]
            for card in cards
        ]
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↻ Refresh",
                    "request_view",
                    {"id": request.id},
                )
            ]
        )
        rows.append(menu_row())
        await session.commit()
    details = request.description or "No description."
    details += f"\n\n{len(matches)} matching card{'s' if len(matches) != 1 else ''}"
    if len(matches) > REQUEST_RESULT_LIMIT:
        details += f" (showing first {REQUEST_RESULT_LIMIT})"
    await send_registered(
        message,
        services,
        f"<b>{html.escape(request.name)}</b>\n{html.escape(details)}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=request.id,
    )


@router.message(Command("memory"))
async def command_memory(message: Message, services: Services) -> None:
    snapshot = await services.memory.sync()
    if not snapshot.valid:
        text = "<b>memory.md needs attention</b>\n" + html.escape(snapshot.error or "Invalid file")
    else:
        text = f"<b>Persistent memory</b> · {snapshot.estimated_tokens}/4000 tokens\n" + (
            "\n".join(f"{i}. {html.escape(fact)}" for i, fact in enumerate(snapshot.facts, 1))
            or "Empty"
        )
    await send_registered(message, services, text, kind=MessageKind.DASHBOARD)


@router.message(Command("syncmem"))
async def command_syncmem(message: Message, services: Services) -> None:
    if (message.text or "").partition(" ")[2].strip():
        await send_registered(message, services, "Usage: /syncmem", kind=MessageKind.ERROR)
        return
    if services.guard.active:
        await send_registered(
            message,
            services,
            "Wait for the current advisor response, then retry /syncmem.",
            kind=MessageKind.ERROR,
        )
        return
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    result = await services.continuity.maintain_memory(message.chat.id)
    if result == MemoryMaintenanceResult.UPDATED:
        await record_memory_run(services.sessions)
        text, kind = "Memory synchronized from Telegram dialogue.", MessageKind.RECEIPT
    elif result == MemoryMaintenanceResult.CURRENT:
        await record_memory_run(services.sessions)
        text, kind = "Memory is already synchronized.", MessageKind.RECEIPT
    elif result == MemoryMaintenanceResult.BUSY:
        text, kind = "Memory synchronization is already running.", MessageKind.ERROR
    elif result == MemoryMaintenanceResult.BOUNDARY_MISSING:
        text, kind = (
            "No /newsession or Summary boundary was found in Telegram. "
            "Start with /newsession followed by your initial request.",
            MessageKind.ERROR,
        )
    else:
        text, kind = "memory.md needs attention; synchronization was not run.", MessageKind.ERROR
    await send_registered(message, services, text, kind=kind)


@router.message(Command("mem"))
async def command_remember(message: Message, services: Services) -> None:
    fact = (message.text or "").partition(" ")[2].strip()
    if not fact:
        await send_registered(
            message, services, "Usage: /mem one durable fact", kind=MessageKind.ERROR
        )
        return
    await services.memory.append_manual(fact)
    await send_registered(message, services, "Remembered in memory.md.", kind=MessageKind.RECEIPT)


@router.message(Command("forget"))
async def command_forget(message: Message, services: Services) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    try:
        line = int(raw)
        await services.memory.forget_line(line)
    except (ValueError, MemoryFileError) as error:
        await send_registered(message, services, html.escape(str(error)), kind=MessageKind.ERROR)
        return
    await send_registered(
        message, services, "Forgotten and memory.md updated.", kind=MessageKind.RECEIPT
    )


@router.message(Command("retro"))
async def command_retro(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        sprint = await session.scalar(
            select(Sprint).where(Sprint.status == "finished").order_by(Sprint.number.desc())
        )
        if sprint is None:
            await send_registered(
                message,
                services,
                "No finished Sprint yet.",
                kind=MessageKind.DASHBOARD,
                markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
            )
            return
        data = await retrospective_data(session, sprint.id)
    png = render_retrospective_png(data)
    sent = await message.answer_photo(
        BufferedInputFile(png, filename=f"sprint-{sprint.number}-retro.png"),
        caption=f"Sprint {sprint.number} retrospective\n"
        + "\n".join(retrospective_recommendations(data)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[retro_back_row()]),
    )
    async with services.sessions() as session:
        await register_message(
            session, sent.chat.id, sent.message_id, "out", MessageKind.RETROSPECTIVE_PNG, sprint.id
        )
        await session.commit()


async def render_feedback(
    message: Message, services: Services, *, notice: str | None = None
) -> None:
    async with services.sessions() as session:
        pending = list(
            await session.scalars(
                select(FeedbackQueue)
                .where(FeedbackQueue.answered_at.is_(None))
                .order_by(FeedbackQueue.created_at)
            )
        )
        if not pending:
            await send_registered(
                message,
                services,
                with_notice("No completion feedback pending.", notice),
                kind=MessageKind.DASHBOARD,
                markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
            )
            return
        item = pending[0]
        card = await session.get(Card, item.card_id)
        yes = await token_button(
            session, services.owner_id, "Yes 🙂", "feedback", {"id": item.id, "liked": True}
        )
        no = await token_button(
            session, services.owner_id, "No 🙁", "feedback", {"id": item.id, "liked": False}
        )
        await session.commit()
    await send_registered(
        message,
        services,
        with_notice(
            f"<b>Feedback 1/{len(pending)}</b>\n"
            f"Did you like doing <b>{html.escape(card.title)}</b>?",
            notice,
        ),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[yes, no], menu_row()]),
        related_id=card.id,
    )


@router.message(Command("feedback"))
async def command_feedback(message: Message, services: Services) -> None:
    await render_feedback(message, services)


@router.message(Command("settings"))
async def command_settings(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        profile = await session.get(UserProfile, 1)
        workspace = await session.get(Workspace, 1)
        if profile is None or workspace is None:
            raise DomainError("Workspace is not initialized")
        memory_update_time = (
            profile.memory_update_time.strftime("%H:%M") if profile.memory_update_time else "off"
        )
        about_me = html.escape(profile.about_me or "—")
        advisor_instructions = html.escape(profile.advisor_instructions or "—")
        wake_time = profile.wake_time or "—"
        bed_time = profile.bed_time or "—"
        quiet_start = profile.quiet_start or "—"
        quiet_end = profile.quiet_end or "—"
        capacity = profile.capacity_effort_points or "—"
        timezone = workspace.timezone
    await send_registered(
        message,
        services,
        "<b>Settings</b>\n"
        f"About me: {about_me}\n"
        f"Advisor instructions: {advisor_instructions}\n"
        f"Wake/bed: {wake_time} / {bed_time}\n"
        f"Quiet hours: {quiet_start}–{quiet_end}\n"
        f"Sprint capacity: {capacity} EP\n"
        f"Memory sync: {memory_update_time} ({timezone})\n"
        "Edit with /setabout, /setadvisor, /setwake, /setbed, /setquiet, /setcapacity, "
        "or /setmemtime HH:MM|off.",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[menu_row()]),
    )


@router.message(Command("setabout"))
async def command_setabout(message: Message, services: Services) -> None:
    value = (message.text or "").partition(" ")[2].strip()
    async with services.sessions() as session:
        await update_profile(session, about_me=value)
        await session.commit()
    await send_registered(message, services, "About Me updated.", kind=MessageKind.RECEIPT)


@router.message(Command("setadvisor"))
async def command_setadvisor(message: Message, services: Services) -> None:
    value = (message.text or "").partition(" ")[2].strip()
    async with services.sessions() as session:
        await update_profile(session, advisor_instructions=value)
        await session.commit()
    await send_registered(
        message, services, "Advisor Instructions updated.", kind=MessageKind.RECEIPT
    )


async def update_profile_field(
    message: Message, services: Services, field: str, value: Any
) -> None:
    async with services.sessions() as session:
        await update_profile(session, **{field: value})
        await session.commit()
    await send_registered(message, services, "Settings updated.", kind=MessageKind.RECEIPT)


@router.message(Command("setwake"))
async def command_setwake(message: Message, services: Services) -> None:
    value = datetime.strptime((message.text or "").partition(" ")[2].strip(), "%H:%M").time()
    await update_profile_field(message, services, "wake_time", value)


@router.message(Command("setbed"))
async def command_setbed(message: Message, services: Services) -> None:
    value = datetime.strptime((message.text or "").partition(" ")[2].strip(), "%H:%M").time()
    await update_profile_field(message, services, "bed_time", value)


@router.message(Command("setquiet"))
async def command_setquiet(message: Message, services: Services) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    start, end = [datetime.strptime(item.strip(), "%H:%M").time() for item in raw.split("-", 1)]
    async with services.sessions() as session:
        await update_profile(session, quiet_start=start, quiet_end=end)
        await session.commit()
    await send_registered(message, services, "Quiet hours updated.", kind=MessageKind.RECEIPT)


@router.message(Command("snooze"))
async def command_snooze(message: Message, services: Services) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    minutes = int(raw) if raw else 60
    if not 1 <= minutes <= 24 * 60:
        raise DomainError("Snooze must be between 1 and 1440 minutes")
    async with services.sessions() as session:
        await snooze_reminders(session, datetime.now(UTC) + timedelta(minutes=minutes))
        await session.commit()
    await send_registered(
        message, services, f"Reminders snoozed for {minutes} minutes.", kind=MessageKind.RECEIPT
    )


@router.message(Command("setcapacity"))
async def command_setcapacity(message: Message, services: Services) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    value = int(raw) if raw else None
    if value is not None and value <= 0:
        raise DomainError("Capacity must be positive or omitted")
    await update_profile_field(message, services, "capacity_effort_points", value)


@router.message(Command("setmemtime"))
async def command_setmemtime(message: Message, services: Services) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    try:
        value = parse_memory_update_time(raw)
    except ValueError as error:
        await send_registered(message, services, html.escape(str(error)), kind=MessageKind.ERROR)
        return
    await update_profile_field(message, services, "memory_update_time", value)


@router.message(Command("status"))
async def command_status(message: Message, services: Services) -> None:
    memory = await services.memory.sync()
    async with services.sessions() as session:
        workspace = await session.get(Workspace, 1)
        feedback = (
            await session.scalar(
                select(func.count(FeedbackQueue.id)).where(FeedbackQueue.answered_at.is_(None))
            )
            or 0
        )
    await send_registered(
        message,
        services,
        f"<b>Status</b>\nMode: {workspace.mode}\nRevision: {workspace.revision}\n"
        f"Feedback: {feedback}\nMemory: {'OK' if memory.valid else 'ERROR'}",
        kind=MessageKind.DASHBOARD,
    )


@router.message(Command("cancel"))
async def command_cancel(message: Message, services: Services) -> None:
    services.guard.cancel()
    await send_registered(
        message, services, "Current generation cancelled.", kind=MessageKind.RECEIPT
    )


@router.callback_query(F.data.startswith("nav:"))
async def navigation(callback: CallbackQuery, services: Services) -> None:
    await callback.answer()
    if not callback.message:
        return
    action = callback.data.split(":", 1)[1]
    if action == "retro_back":
        # Telegram cannot turn a photo message into a text message.  The screen
        # underneath is still the menu that opened the retrospective, so remove
        # only the media screen rather than sending an unnecessary new message.
        await callback.message.delete()
        return
    handlers = {
        "home": command_start,
        "today": command_today,
        "sprint": command_sprint,
        "backlog": command_backlog,
        "add": command_add,
        "values": command_values,
        "tags": command_tags,
        "requests": command_requests,
        "advisor": command_advisor,
        "retro": command_retro,
        "settings": command_settings,
    }
    handler = handlers.get(action)
    if handler is None:
        logger.warning("Unknown nav action: %s", action)
        await callback.answer("This action is no longer available.", show_alert=True)
        return
    if action != "add":
        async with services.sessions() as session:
            await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
            await session.commit()
    await handler(callback.message, services)
