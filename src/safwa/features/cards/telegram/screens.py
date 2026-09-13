"""One Card, whole: what it holds, what it is under, and everything it can be told to do."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    Services,
    edit_registered_message,
    send_registered,
    token_button,
    with_notice,
)
from tg_agent_shell.telegram.model import UiSession

from ....foundation.marks import live_repeat_instance_id, title_marks
from ....foundation.workspace import Workspace
from ...checks.use_cases import card_checks
from ...tags.model import CardTag, Tag
from ...values.model import CardValue, Value
from ..hard_time import hard_time_text
from ..hierarchy import blocking_actions, card_progress
from ..model import Card, CardCategory, CardEnergyType, CardKind, CardStage
from .presentation import card_overview_text, card_title_marks


async def render_card(
    message: Message,
    services: Services,
    card_id: int,
    *,
    replace_message_id: int | None = None,
    back: dict[str, Any] | None = None,
    full: bool | None = None,
    notice: str | None = None,
    replace: bool | None = None,
) -> None:
    """Draw one Card, compact by default; `full` is remembered until it is changed."""
    async with services.sessions() as session:
        existing_editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == services.owner_id)
        )
        remembered: dict[str, Any] = (
            existing_editor.state
            if existing_editor is not None
            and existing_editor.kind == "card_editor"
            and existing_editor.state.get("card_id") == card_id
            else {}
        )
        if back is None:
            back = dict(remembered.get("back", {}))
        if full is None:
            # An edit redraws the Card, and it redraws it in the view it was made in.
            full = bool(remembered.get("full", False))
        back = back or {}
        card = await session.get(Card, card_id)
        if card is None:
            raise DomainError("Card does not exist")
        parent = await session.get(Card, card.parent_id) if card.parent_id else None
        direct_value_ids = list(
            await session.scalars(select(CardValue.value_id).where(CardValue.card_id == card.id))
        )
        direct_values = (
            list(await session.scalars(select(Value).where(Value.id.in_(direct_value_ids))))
            if direct_value_ids
            else []
        )
        direct_tag_ids = list(
            await session.scalars(select(CardTag.tag_id).where(CardTag.card_id == card.id))
        )
        direct_tags = (
            list(await session.scalars(select(Tag).where(Tag.id.in_(direct_tag_ids))))
            if direct_tag_ids
            else []
        )
        categories = list(
            await session.scalars(
                select(CardCategory.category).where(CardCategory.card_id == card.id)
            )
        )
        energy_types = list(
            await session.scalars(
                select(CardEnergyType.energy_type).where(CardEnergyType.card_id == card.id)
            )
        )
        archived = card.archived_at is not None
        # An archived Card has no control to put away, so it opens whole.
        full = full or archived
        # Every control that edits a field belongs to the full view.
        field_specs: list[tuple[str, str, dict[str, Any]]] = (
            []
            if archived or not full
            else [
                ("✏️ Title", "card_edit_text", {"id": card.id, "field": "title"}),
                ("📝 Note", "card_edit_text", {"id": card.id, "field": "note"}),
                ("⚠️ Priority", "card_choose_priority", {"id": card.id}),
                (
                    "⏱ Hard Time",
                    "card_edit_text",
                    {"id": card.id, "field": "hard_time"},
                ),
            ]
        )
        if full and not archived and card.hard_time is not None:
            field_specs.append(
                (
                    "📝 Hard Time note",
                    "card_edit_text",
                    {"id": card.id, "field": "hard_time_description"},
                )
            )
        if full and card.kind == CardKind.ACTION.value and not archived:
            field_specs.insert(2, ("📍 Stage", "card_choose_stage", {"id": card.id}))
            field_specs.append(
                ("🚧 Blocked", "card_toggle_field", {"id": card.id, "field": "blocked"})
            )
            if card.blocked:
                field_specs.append(
                    (
                        "📝 Blocked reason",
                        "card_edit_text",
                        {"id": card.id, "field": "blocked_description"},
                    )
                )
            field_specs.extend(
                [
                    ("🔢 Effort", "card_choose_effort", {"id": card.id}),
                    (
                        "🔁 Repeat",
                        "card_toggle_field",
                        {"id": card.id, "field": "repeatable"},
                    ),
                    ("🏷 Categories", "card_choose_categories", {"id": card.id}),
                    ("⚡ Energy", "card_choose_energy", {"id": card.id}),
                ]
            )
        if full and not archived:
            field_specs.extend(
                [
                    ("💎 Values", "card_choose_values", {"id": card.id}),
                    ("🏷 Tags", "card_choose_tags", {"id": card.id}),
                ]
            )
        buttons = [await token_button(session, services.owner_id, *spec) for spec in field_specs]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        relationship_rows: list[list[InlineKeyboardButton]] = []
        if parent is not None:
            relationship_rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"🌳 Parent: {parent.title}"[:60],
                        "card_view",
                        {
                            "id": parent.id,
                            "back": {"action": "card_view", "id": card.id, "back": back},
                        },
                    )
                ]
            )
        live_id = await live_repeat_instance_id(session, card) if card.is_closed_repeat() else None
        live_card = await session.get(Card, live_id) if live_id is not None else None
        if live_card is not None:
            relationship_rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"🔄 Current: {live_card.title}"[:60],
                        "card_view",
                        {
                            "id": live_card.id,
                            "back": {"action": "card_view", "id": card.id, "back": back},
                        },
                    )
                ]
            )
        if card.kind in {CardKind.GOAL.value, CardKind.SUBGOAL.value}:
            relationship_rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        "👥 Children",
                        "card_children",
                        {"id": card.id, "page": 0, "back": back},
                    )
                ]
            )
        # Every Value on the Card is a button, so the one on a Goal opens from the Goal.
        value_buttons = (
            [
                await token_button(
                    session,
                    services.owner_id,
                    f"💎 {value.name}"[:60],
                    "value_view",
                    {"id": value.id},
                )
                for value in direct_values
            ]
            if full or card.kind == CardKind.GOAL.value
            else []
        )
        relationship_rows.extend(
            value_buttons[index : index + 2] for index in range(0, len(value_buttons), 2)
        )
        direct_checks = await card_checks(session, card.id)
        check_total = len(direct_checks)
        pending_total = sum(1 for check in direct_checks if check.outcome is None)
        # A Check reaches a Card through a proposal, so an empty list has nothing to offer.
        if direct_checks:
            relationship_rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        f"☑️ Checks ({pending_total}/{check_total})",
                        "check_list",
                        {
                            "card_id": card.id,
                            "back": {"action": "card_view", "id": card.id, "back": back},
                        },
                    )
                ]
            )
        primary_row: list[InlineKeyboardButton] = []
        if (
            card.kind == CardKind.ACTION.value
            and card.effective_stage != CardStage.DONE.value
        ):
            primary_row.append(
                await token_button(
                    session, services.owner_id, "✅ Done", "card_finish", {"id": card.id}
                )
            )
        if not full and card.kind == CardKind.ACTION.value and not archived:
            # The one field the compact view still sets: where the Action stands today.
            primary_row.append(
                await token_button(
                    session,
                    services.owner_id,
                    "📍 Stage",
                    "card_choose_stage",
                    {"id": card.id},
                )
            )
        rows = ([primary_row] if primary_row else []) + relationship_rows + rows
        if full:
            # What an archived Card still offers: it leaves the archive by being
            # reopened, and a closed repeat never reopens, so the only way out is Delete.
            closing_row = [
                await token_button(
                    session,
                    services.owner_id,
                    "Delete",
                    "card_delete_prompt",
                    {"id": card.id},
                )
            ]
            if archived:
                if card.kind == CardKind.ACTION.value and not card.is_closed_repeat():
                    closing_row.insert(
                        0,
                        await token_button(
                            session,
                            services.owner_id,
                            "♻️ Reopen",
                            "card_move",
                            {"id": card.id, "stage": CardStage.BACKLOG.value},
                        ),
                    )
            else:
                closing_row.insert(
                    0,
                    await token_button(
                        session,
                        services.owner_id,
                        "Archive",
                        "card_archive",
                        {"id": card.id},
                    ),
                )
            rows.append(closing_row)
        last_row = [
            await token_button(
                session, services.owner_id, "↩️ Back", "card_back", {"back": back}
            )
        ]
        if not archived:
            last_row.insert(
                0,
                await token_button(
                    session,
                    services.owner_id,
                    "🗜 Compact" if full else "✏️ Full editing",
                    "card_view_mode",
                    {"id": card.id, "full": not full},
                ),
            )
        rows.append(last_row)
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        # An archived Card has no field to type into, so it leaves no editor behind.
        if not archived:
            session.add(
                UiSession(
                    owner_id=services.owner_id,
                    kind="card_editor",
                    state={
                        "card_id": card.id,
                        "back": back,
                        "full": full,
                        "message_id": replace_message_id or message.message_id,
                    },
                    expires_at=datetime.now(UTC) + timedelta(minutes=30),
                )
            )
        progress: dict[str, Any] = {}
        if card.kind != CardKind.ACTION.value:
            progress = dict(await card_progress(session, card.id))
            progress["blocking_actions"] = [
                (action.title, action.blocked_description)
                for action in await blocking_actions(session, card.id)
            ]
        workspace = await session.get(Workspace, 1)
        tz = ZoneInfo(workspace.timezone if workspace else "UTC")
        card_marks = await card_title_marks(session, card)
        check_names = [
            check.title + await title_marks(session, check) for check in direct_checks
        ]
        closed_at = card.completed_at
        await session.commit()
    text = with_notice(
        card_overview_text(
            {
                "kind": card.kind,
                "title": card.title + card_marks,
                "parent_name": parent.title if parent else None,
                "stage": card.effective_stage,
                "closed_at": f"{closed_at.astimezone(tz):%Y-%m-%d %H:%M}" if closed_at else None,
                "note": card.note,
                "priority": card.priority,
                "hard_time": hard_time_text(card.hard_time, tz=tz),
                "hard_time_description": card.hard_time_description,
                "blocked": card.blocked,
                "blocked_description": card.blocked_description,
                "effort_points": card.effort_points,
                "repeatable": card.repeatable,
                "categories": categories,
                "energy_types": energy_types,
                "value_names": [value.name for value in direct_values],
                "tag_names": [tag.name for tag in direct_tags],
                "check_names": check_names,
                **progress,
            },
            compact=not full,
        ),
        notice,
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=card.id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.DASHBOARD,
            markup=markup,
            related_id=card.id,
            replace=replace,
        )
