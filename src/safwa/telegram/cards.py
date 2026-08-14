from __future__ import annotations

import html
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import EFFORT_POINTS, SELECTOR_PAGE_SIZE
from ..domain import (
    DomainError,
    ReferenceSpec,
    card_progress,
    validate_action_fields,
    validate_blocked_fields,
)
from ..enums import (
    CardKind,
    CardStage,
    Category,
    EnergyType,
    MessageKind,
    Priority,
)
from ..models import (
    Card,
    CardCategory,
    CardCheck,
    CardEnergyType,
    CardTag,
    CardValue,
    Check,
    Tag,
    UiSession,
    Value,
)
from ._core import (
    CARD_CHOICE_FIELDS,
    CHOICE_TITLES,
    NAMED_CHOICE_FIELDS,
    RELATION_CHOICES,
    SINGLE_CHOICE_FIELDS,
    RelationChoice,
    Services,
    sprint_is_active,
)
from ._messaging import edit_registered_message, paging_row, send_registered, token_button
from ._presentation import (
    CATEGORY_EMOJIS,
    ENERGY_EMOJIS,
    Page,
    card_overview_text,
    kind_label,
    menu_markup,
    menu_row,
    paginate,
    paginate_cards,
    typed_label,
    with_notice,
)

logger = logging.getLogger(__name__)


async def linked_card_count(session: AsyncSession, spec: ReferenceSpec, item_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(spec.link_model).where(spec.link_column == item_id)
        )
        or 0
    )


# Which side a one-tap stage move sits on, so a row always reads the same way: leaving
# Today for the Sprint on the left, pulling a Sprint Action into Today on the right.
QUICK_MOVE_BUTTONS = {
    CardStage.SPRINT: ("🏃", True),
    CardStage.TODAY: ("☀️", False),
}


async def card_list_rows(
    session: AsyncSession,
    services: Services,
    cards: list[Card],
    *,
    page: int,
    back: dict[str, Any],
    quick_move: CardStage | None = None,
    prefix: Callable[[Card], str] | None = None,
) -> tuple[Page, list[str], list[list[InlineKeyboardButton]]]:
    """One Card list: the page, its plain-text lines, and one button row per Card."""
    current = paginate_cards(cards, page)
    back = {**back, "page": current.index}
    rows: list[list[InlineKeyboardButton]] = []
    descriptions: list[str] = []
    for card in current.items:
        metadata = [
            kind_label(card.kind),
            card.priority.title(),
            f"{card.effort_points or '—'} EP",
        ]
        if card.hard_time:
            metadata.append("Hard time")
        if card.repeatable:
            metadata.append("Repeat")
        if card.blocked:
            metadata.append("Blocked")
        label = f"{prefix(card) if prefix else ''}{card.title} · {' · '.join(metadata)}"
        descriptions.append(f"• {label}")
        row = [
            await token_button(
                session,
                services.owner_id,
                label[: 40 if quick_move else 60],
                "card_view",
                {"id": card.id, "back": back},
            )
        ]
        if quick_move is not None:
            emoji, leading = QUICK_MOVE_BUTTONS[quick_move]
            move = await token_button(
                session,
                services.owner_id,
                emoji,
                "card_quick_move",
                {"id": card.id, "stage": quick_move.value, "back": back},
            )
            row.insert(0 if leading else 1, move)
        rows.append(row)
    return current, descriptions, rows


def card_list_text(title: str, page: Page, descriptions: list[str], *, header: str = "") -> str:
    body = "\n".join(html.escape(description) for description in descriptions)
    return (
        f"<b>{html.escape(title)}</b> · {page.label}\n"
        + (f"{header}\n" if header else "")
        + (body or "Nothing here yet.")
    )


async def render_dashboard(
    message: Message,
    services: Services,
    stage: CardStage,
    *,
    title: str,
    page: int = 0,
    notice: str | None = None,
) -> None:
    async with services.sessions() as session:
        cards = list(
            await session.scalars(
                select(Card).where(
                    Card.effective_stage == stage.value,
                    Card.kind == CardKind.ACTION.value,
                    Card.archived_at.is_(None),
                )
            )
        )
        current, descriptions, rows = await card_list_rows(
            session,
            services,
            cards,
            page=page,
            back={"kind": "dashboard", "stage": stage.value, "title": title},
        )
        rows.extend(
            await paging_row(
                session,
                services.owner_id,
                current,
                "dashboard_page",
                {"stage": stage.value, "title": title},
            )
        )
        rows.append(menu_row())
        await session.commit()
    await send_registered(
        message,
        services,
        with_notice(card_list_text(title, current, descriptions), notice),
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _new_card_creation_state() -> dict[str, Any]:
    return {
        "kind": CardKind.ACTION.value,
        "title": "",
        "note": "",
        "stage": CardStage.BACKLOG.value,
        "priority": Priority.MEDIUM.value,
        "hard_time": False,
        "blocked": False,
        "blocked_description": "",
        "effort_points": None,
        "repeatable": False,
        "categories": [],
        "energy_types": [],
        "value_ids": [],
        "tag_ids": [],
    }


def sanitize_card_creation_state(state: dict[str, Any]) -> dict[str, Any]:
    clean = {**_new_card_creation_state(), **state}
    try:
        clean["kind"] = CardKind(clean["kind"]).value
    except ValueError:
        clean["kind"] = CardKind.ACTION.value
    try:
        clean["stage"] = CardStage(clean["stage"]).value
    except ValueError:
        clean["stage"] = CardStage.BACKLOG.value
    if clean["stage"] in {CardStage.DONE.value, CardStage.CANCELLED.value}:
        clean["stage"] = CardStage.BACKLOG.value
    if clean["kind"] != CardKind.ACTION.value:
        clean.update(
            effort_points=None,
            repeatable=False,
            categories=[],
            energy_types=[],
        )
    if not clean["blocked"]:
        clean["blocked_description"] = ""
    for field in ("categories", "energy_types", "value_ids", "tag_ids"):
        clean[field] = list(dict.fromkeys(clean.get(field) or []))
    return clean


def card_creation_errors(state: dict[str, Any]) -> list[str]:
    """Report what still blocks Save, using the same rules the domain enforces.

    The draft is checked here only so Save can be hidden until it would succeed;
    ``create_card`` remains the authority and revalidates everything.
    """
    errors: list[str] = []
    if not str(state.get("title", "")).strip():
        errors.append("Add a title")
    for check in (
        lambda: validate_action_fields(
            state["kind"],
            state.get("effort_points"),
            bool(state.get("repeatable")),
            set(state.get("categories") or []),
            set(state.get("energy_types") or []),
        ),
        lambda: validate_blocked_fields(
            bool(state.get("blocked")), state.get("blocked_description")
        ),
    ):
        try:
            check()
        except DomainError as error:
            errors.append(str(error))
    return errors


async def card_creation_markup(
    session: AsyncSession, services: Services, state: dict[str, Any]
) -> InlineKeyboardMarkup:
    fields: list[tuple[str, str, dict[str, Any]]] = [
        ("🧩 Kind", "card_create_choose_kind", {}),
        ("✏️ Title", "card_create_edit_text", {"field": "title"}),
        ("📍 Stage", "card_create_choose_stage", {}),
        ("📝 Note", "card_create_edit_text", {"field": "note"}),
        ("⚠️ Priority", "card_create_choose_priority", {}),
        ("⏱ Hard Time", "card_create_toggle", {"field": "hard_time"}),
        ("🚧 Blocked", "card_create_toggle", {"field": "blocked"}),
    ]
    if state.get("blocked"):
        fields.append(
            (
                "📝 Blocked reason",
                "card_create_edit_text",
                {"field": "blocked_description"},
            )
        )
    if state["kind"] == CardKind.ACTION.value:
        fields.extend(
            [
                ("🔢 Effort", "card_create_choose_effort", {}),
                ("🔁 Repeat", "card_create_toggle", {"field": "repeatable"}),
                ("🏷 Categories", "card_create_choose_categories", {}),
                ("⚡ Energy", "card_create_choose_energy", {}),
            ]
        )
    fields.extend(
        [
            ("💎 Values", "card_create_choose_values", {}),
            ("🏷 Tags", "card_create_choose_tags", {}),
        ]
    )
    buttons = [await token_button(session, services.owner_id, *field) for field in fields]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    if not card_creation_errors(state):
        rows.append([await token_button(session, services.owner_id, "✅ Save", "card_create_save")])
    rows.append(
        [await token_button(session, services.owner_id, "🗑 Discard", "card_create_discard")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_card_creation(
    message: Message,
    services: Services,
    *,
    replace_message_id: int | None = None,
) -> None:
    async with services.sessions() as session:
        editor = await session.scalar(
            select(UiSession).where(
                UiSession.owner_id == services.owner_id,
                UiSession.kind == "card_create",
            )
        )
        if editor is None:
            await send_registered(
                message,
                services,
                "Card creation is no longer active.",
                kind=MessageKind.ERROR,
                markup=menu_markup(sprint_active=await sprint_is_active(session)),
            )
            return
        state = sanitize_card_creation_state(dict(editor.state or {}))
        editor.state = state
        value_ids = list(state["value_ids"])
        tag_ids = list(state["tag_ids"])
        values = (
            list(await session.scalars(select(Value).where(Value.id.in_(value_ids))))
            if value_ids
            else []
        )
        tags = (
            list(await session.scalars(select(Tag).where(Tag.id.in_(tag_ids)))) if tag_ids else []
        )
        display = {
            **state,
            "value_names": [value.name for value in values],
            "tag_names": [tag.name for tag in tags],
        }
        text = card_overview_text(display, heading="Create Card")
        errors = card_creation_errors(state)
        if errors:
            text += "\n\n" + "\n".join(f"⚠️ {html.escape(error)}" for error in errors)
        markup = await card_creation_markup(session, services, state)
        editor_id = editor.id
        await session.commit()
    if replace_message_id is not None:
        await edit_registered_message(
            message,
            services,
            replace_message_id,
            text,
            kind=MessageKind.CARD_EDITOR,
            markup=markup,
            related_id=editor_id,
        )
    else:
        await send_registered(
            message,
            services,
            text,
            kind=MessageKind.CARD_EDITOR,
            markup=markup,
            related_id=editor_id,
        )


async def start_manual_card_creation(message: Message, services: Services) -> None:
    async with services.sessions() as session:
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="card_create",
                state=_new_card_creation_state(),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        await session.commit()
    await render_card_creation(message, services)


async def choice_screen(
    message: Message,
    services: Services,
    title: str,
    choices: list[tuple[str, str, dict[str, Any]]],
    *,
    back: tuple[str, str, dict[str, Any]] | None = None,
    paging: tuple[Page, str, dict[str, Any]] | None = None,
) -> None:
    heading = title if paging is None or paging[0].count == 1 else f"{title} · {paging[0].label}"
    async with services.sessions() as session:
        rows = [
            [await token_button(session, services.owner_id, text, action, payload)]
            for text, action, payload in choices
        ]
        if paging is not None:
            page, page_action, page_payload = paging
            rows.extend(
                await paging_row(session, services.owner_id, page, page_action, page_payload)
            )
        if back:
            rows.append([await token_button(session, services.owner_id, *back)])
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>{html.escape(heading)}</b>",
        kind=MessageKind.CARD_EDITOR,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def require_card_draft(session: AsyncSession, owner_id: int) -> UiSession:
    draft = await session.scalar(
        select(UiSession).where(
            UiSession.owner_id == owner_id,
            UiSession.kind == "card_create",
        )
    )
    if draft is None:
        raise DomainError("Card creation is no longer active")
    return draft


async def card_editor_back_state(session: AsyncSession, owner_id: int) -> dict[str, Any]:
    """Keep the navigation trail of the Card screen a focused prompt replaces."""
    editor = await session.scalar(select(UiSession).where(UiSession.owner_id == owner_id))
    if editor is None or editor.kind != "card_editor":
        return {}
    return dict(editor.state.get("back", {}))


async def _choice_options(session: AsyncSession, field: str) -> list[tuple[str, Any]]:
    """The selectable options for one Card field, shared by draft and committed screens."""
    if field == "kind":
        return [(kind_label(kind), kind.value) for kind in CardKind]
    if field == "stage":
        return [
            (stage.value.title(), stage.value)
            for stage in (CardStage.BACKLOG, CardStage.SPRINT, CardStage.TODAY)
        ]
    if field == "priority":
        return [(priority.value.title(), priority.value) for priority in Priority]
    if field == "effort":
        return [(f"{points} EP", points) for points in sorted(EFFORT_POINTS)]
    if field == "categories":
        return [(typed_label(item, CATEGORY_EMOJIS), item.value) for item in Category]
    if field == "energy":
        return [(typed_label(item, ENERGY_EMOJIS), item.value) for item in EnergyType]
    if field not in {"values", "tags"}:
        raise DomainError("Unknown Card selector")
    model = Value if field == "values" else Tag
    items = await session.scalars(
        select(model).where(model.archived_at.is_(None)).order_by(model.name)
    )
    return [(item.name, item.id) for item in items]


def _choice_rows(
    options: list[tuple[str, Any]],
    selected: set[Any],
    build: Callable[[Any], tuple[str, dict[str, Any]]],
) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for label, value in options:
        action, payload = build(value)
        rows.append((f"{'✓ ' if value in selected else ''}{label}", action, payload))
    return rows


def _selector_page(field: str, options: list[tuple[str, Any]], page: int) -> Page | None:
    """Page the Value and Tag lists; the fixed enumerations always fit one screen."""
    if field not in NAMED_CHOICE_FIELDS:
        return None
    return paginate(options, page, SELECTOR_PAGE_SIZE)


async def handle_card_creation_chooser(
    message: Message, services: Services, action: str, *, page: int = 0
) -> None:
    """Render one selector for the transient Card draft; nothing is committed here."""
    field = action.removeprefix("card_create_choose_")
    relation = RELATION_CHOICES.get(field)
    async with services.sessions() as session:
        draft = await require_card_draft(session, services.owner_id)
        state = sanitize_card_creation_state(dict(draft.state or {}))
        options = await _choice_options(session, field)
        current = _selector_page(field, options, page)
        if relation is not None:
            selected = set(state[relation.draft_field])

            def build(value: Any, relation: RelationChoice = relation) -> tuple[str, dict]:
                return (
                    f"card_create_toggle_{relation.singular}",
                    {relation.payload_key: value},
                )
        else:
            state_field = SINGLE_CHOICE_FIELDS[field]
            selected = {state[state_field]}

            def build(value: Any, state_field: str = state_field) -> tuple[str, dict]:
                return "card_create_set", {"field": state_field, "value": value}

        choices = _choice_rows(current.items if current else options, selected, build)
        await session.commit()
    await choice_screen(
        message,
        services,
        CHOICE_TITLES[field],
        choices,
        back=("↩️ Back", "card_create_view", {}),
        paging=(current, action, {}) if current else None,
    )


async def render_card_choices(
    message: Message, services: Services, action: str, card_id: int, *, page: int = 0
) -> None:
    """Render field and relationship selectors for an already committed Card.

    Every selection routes its mutation through the domain layer, and the screens
    themselves stay out of the persona dialogue.
    """
    field = action.removeprefix("card_choose_")
    if field not in CARD_CHOICE_FIELDS:
        raise DomainError("Unknown Card relationship selector")
    relation = RELATION_CHOICES.get(field)
    async with services.sessions() as session:
        card = await session.get(Card, card_id)
        if card is None or card.archived_at is not None:
            raise DomainError("Card does not exist or is archived")
        options = await _choice_options(session, field)
        current = _selector_page(field, options, page)
        if relation is not None:
            selected = set(
                await session.scalars(
                    select(relation.link_column).where(relation.link_owner == card.id)
                )
            )

            def build(value: Any, relation: RelationChoice = relation) -> tuple[str, dict]:
                return (
                    f"card_toggle_{relation.singular}",
                    {"id": card.id, relation.payload_key: value},
                )
        elif field == "stage":
            # A stage change is a domain move, not a plain field write.
            selected = {card.effective_stage}

            def build(value: Any) -> tuple[str, dict]:
                return "card_move", {"id": card.id, "stage": value}

        else:
            column = SINGLE_CHOICE_FIELDS[field]
            selected = {getattr(card, column)}

            def build(value: Any, column: str = column) -> tuple[str, dict]:
                return "card_set_field", {"id": card.id, "field": column, "value": value}

        choices = _choice_rows(current.items if current else options, selected, build)
        await session.commit()
    await choice_screen(
        message,
        services,
        CHOICE_TITLES[field],
        choices,
        back=("↩️ Back", "card_view", {"id": card_id}),
        paging=(current, action, {"id": card_id}) if current else None,
    )


async def render_children(
    message: Message,
    services: Services,
    parent_id: int,
    *,
    page: int = 0,
    back: dict[str, Any] | None = None,
) -> None:
    back = back or {"kind": "home"}
    async with services.sessions() as session:
        parent = await session.get(Card, parent_id)
        if parent is None or parent.archived_at is not None:
            raise DomainError("Parent Card does not exist or is archived")
        children = list(
            await session.scalars(
                select(Card).where(
                    Card.parent_id == parent.id,
                    Card.archived_at.is_(None),
                )
            )
        )
        current = paginate_cards(children, page)
        child_back = {
            "kind": "children",
            "id": parent.id,
            "page": current.index,
            "back": back,
        }
        rows: list[list[InlineKeyboardButton]] = []
        for child in current.items:
            label = f"{kind_label(child.kind)} · {child.title} · {child.effective_stage.title()}"
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        label[:60],
                        "card_view",
                        {"id": child.id, "back": child_back},
                    )
                ]
            )
        rows.extend(
            await paging_row(
                session,
                services.owner_id,
                current,
                "card_children",
                {"id": parent.id, "back": back},
            )
        )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "card_view",
                    {"id": parent.id, "back": back},
                )
            ]
        )
        await session.commit()
    await send_registered(
        message,
        services,
        f"<b>Children of {html.escape(parent.title)}</b> · "
        f"{len(children)} total · {current.label}",
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
        related_id=parent.id,
    )


async def render_card(
    message: Message,
    services: Services,
    card_id: int,
    *,
    replace_message_id: int | None = None,
    back: dict[str, Any] | None = None,
    notice: str | None = None,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
    replace: bool | None = None,
) -> None:
    async with services.sessions() as session:
        existing_editor = await session.scalar(
            select(UiSession).where(UiSession.owner_id == services.owner_id)
        )
        if (
            back is None
            and existing_editor is not None
            and existing_editor.kind == "card_editor"
            and existing_editor.state.get("card_id") == card_id
        ):
            back = dict(existing_editor.state.get("back", {}))
        back = back or {"kind": "home"}
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
        field_specs = [
            ("✏️ Title", "card_edit_text", {"id": card.id, "field": "title"}),
            ("📝 Note", "card_edit_text", {"id": card.id, "field": "note"}),
            ("📍 Stage", "card_choose_stage", {"id": card.id}),
            ("⚠️ Priority", "card_choose_priority", {"id": card.id}),
            ("⏱ Hard Time", "card_toggle_field", {"id": card.id, "field": "hard_time"}),
            ("🚧 Blocked", "card_toggle_field", {"id": card.id, "field": "blocked"}),
        ]
        if card.blocked:
            field_specs.append(
                (
                    "📝 Blocked reason",
                    "card_edit_text",
                    {"id": card.id, "field": "blocked_description"},
                )
            )
        if card.kind == CardKind.ACTION.value:
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
                            "back": {"kind": "card", "id": card.id, "back": back},
                        },
                    )
                ]
            )
        if card.kind in {CardKind.GOAL.value, CardKind.IDEA.value}:
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
        direct_checks = list(
            await session.scalars(
                select(Check)
                .join(CardCheck, CardCheck.check_id == Check.id)
                .where(CardCheck.card_id == card.id, Check.archived_at.is_(None))
                .order_by(Check.id)
            )
        )
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
                        "card_checks",
                        {
                            "card_id": card.id,
                            "back": {"kind": "card", "id": card.id, "back": back},
                        },
                    )
                ]
            )
        rows = relationship_rows + rows
        if card.kind == CardKind.ACTION.value and card.effective_stage not in {
            CardStage.DONE.value,
            CardStage.CANCELLED.value,
        }:
            rows.append(
                [
                    await token_button(
                        session,
                        services.owner_id,
                        "✅ Done",
                        "card_finish",
                        {"id": card.id, "stage": "done"},
                    ),
                    await token_button(
                        session,
                        services.owner_id,
                        "✖ Cancel",
                        "card_finish",
                        {"id": card.id, "stage": "cancelled"},
                    ),
                ]
            )
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "Archive", "card_archive", {"id": card.id}
                ),
                await token_button(
                    session,
                    services.owner_id,
                    "Delete",
                    "card_delete_prompt",
                    {"id": card.id},
                ),
            ]
        )
        rows.append(
            [
                await token_button(
                    session,
                    services.owner_id,
                    "↩️ Back",
                    "card_back",
                    {"back": back},
                )
            ]
        )
        await session.execute(delete(UiSession).where(UiSession.owner_id == services.owner_id))
        session.add(
            UiSession(
                owner_id=services.owner_id,
                kind="card_editor",
                state={
                    "card_id": card.id,
                    "back": back,
                    "message_id": replace_message_id or message.message_id,
                },
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        progress = (
            await card_progress(session, card.id)
            if card.kind in {CardKind.GOAL.value, CardKind.IDEA.value}
            else {}
        )
        await session.commit()
    text = with_notice(
        card_overview_text(
            {
                "kind": card.kind,
                "title": card.title,
                "parent_name": parent.title if parent else None,
                "stage": card.effective_stage,
                "note": card.note,
                "priority": card.priority,
                "hard_time": card.hard_time,
                "blocked": card.blocked,
                "blocked_description": card.blocked_description,
                "effort_points": card.effort_points,
                "repeatable": card.repeatable,
                "categories": categories,
                "energy_types": energy_types,
                "value_names": [value.name for value in direct_values],
                "tag_names": [tag.name for tag in direct_tags],
                "check_names": [check.title for check in direct_checks],
                **progress,
            }
        ),
        notice,
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows + list(extra_rows or []))
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
