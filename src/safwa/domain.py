from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .constants import (
    ARCHIVE_MARKER,
    REPEAT_LIVE,
    REPEAT_MARKER,
)
from .features.cards.model import TERMINAL_STAGES
from .features.cards.model import is_closed_repeat as _is_closed_repeat_card
from .features.cards.use_cases import archive_settled_cards
from .features.cards.use_cases import archive_subtree as archive_subtree
from .features.cards.use_cases import blocking_actions as blocking_actions
from .features.cards.use_cases import card_children as card_children
from .features.cards.use_cases import card_progress as card_progress
from .features.cards.use_cases import create_card as create_card
from .features.cards.use_cases import delete_subtree as delete_subtree
from .features.cards.use_cases import edit_card_text as edit_card_text
from .features.cards.use_cases import finish_action as finish_action
from .features.cards.use_cases import move_card as move_card
from .features.cards.use_cases import set_card_parent as set_card_parent
from .features.cards.use_cases import toggle_card_category as toggle_card_category
from .features.cards.use_cases import toggle_card_check as toggle_card_check
from .features.cards.use_cases import toggle_card_energy_type as toggle_card_energy_type
from .features.cards.use_cases import toggle_card_tag as toggle_card_tag
from .features.cards.use_cases import toggle_card_value as toggle_card_value
from .features.cards.use_cases import update_card_fields as update_card_fields
from .features.cards.use_cases import validate_action_fields as validate_action_fields
from .features.cards.use_cases import validate_blocked_fields as validate_blocked_fields
from .features.checks.model import CheckOutcome as CheckOutcome
from .features.checks.model import is_closed_repeat as _is_closed_repeat_check
from .features.checks.use_cases import archive_check as archive_check
from .features.checks.use_cases import archive_settled_checks
from .features.checks.use_cases import card_checks as card_checks
from .features.checks.use_cases import check_card_id as check_card_id
from .features.checks.use_cases import check_value_ids as check_value_ids
from .features.checks.use_cases import create_check as create_check
from .features.checks.use_cases import delete_check as delete_check
from .features.checks.use_cases import pending_checks as pending_checks
from .features.checks.use_cases import resolve_check as resolve_check
from .features.checks.use_cases import toggle_check_value as toggle_check_value
from .features.checks.use_cases import unobserved_series as unobserved_series
from .features.checks.use_cases import update_check_fields as update_check_fields
from .features.planning.api import settled_cutoff
from .features.planning.use_cases import finish_sprint as _finish_sprint
from .features.planning.use_cases import set_sprint_success_criteria as set_sprint_success_criteria
from .features.planning.use_cases import sprint_is_due
from .features.planning.use_cases import sprint_length_days as sprint_length_days
from .features.planning.use_cases import sprint_metrics as sprint_metrics
from .features.planning.use_cases import start_sprint as start_sprint
from .features.tags.use_cases import create_tag as create_tag
from .features.tags.use_cases import delete_tag as delete_tag
from .features.tags.use_cases import update_tag_fields as update_tag_fields
from .features.values.use_cases import create_value as create_value
from .features.values.use_cases import delete_value as delete_value
from .features.values.use_cases import set_value_focus as set_value_focus
from .features.values.use_cases import update_value_fields as update_value_fields
from .foundation.clock import utcnow as utcnow
from .foundation.errors import DomainError
from .foundation.errors import StaleStateError as StaleStateError
from .models import (
    Card,
    Check,
    Sprint,
    UserProfile,
    Workspace,
)


async def bootstrap_workspace(session: AsyncSession, owner_id: int, timezone: str) -> Workspace:
    workspace = await session.get(Workspace, 1)
    if workspace is None:
        workspace = Workspace(id=1, owner_telegram_id=owner_id, timezone=timezone)
        session.add(workspace)
    elif workspace.owner_telegram_id != owner_id:
        raise DomainError("The database is already bound to another Telegram owner")
    profile = await session.get(UserProfile, 1)
    if profile is None:
        session.add(UserProfile(id=1))
    await session.flush()
    return workspace


def is_closed_repeat(entity: Card | Check) -> bool:
    """A repeat instance that already ended, so its series continues on a newer row.

    Editing one is almost always aimed at the live instance instead, and the edit
    would not reach it: the successor was copied at close time.
    """
    if isinstance(entity, Check):
        return _is_closed_repeat_check(entity)
    return _is_closed_repeat_card(entity)


async def title_marks(session: AsyncSession, entity: Card | Check) -> str:
    """What a title carries after it: its place in a repeat series, and the archive.

    The place is counted over every row the series has ever had, so archiving one does not
    renumber the others, and `live #7` is the open one — a closed instance read as the one
    to work with is the mistake both marks exist to stop.  Nothing is stored renamed:
    `ai_cards` and `ai_checks` render the same marks in SQL.
    """
    marks = ""
    if is_closed_repeat(entity):
        if isinstance(entity, Check):
            statement = (
                select(func.count())
                .select_from(Check)
                .where(Check.series_id == (entity.series_id or entity.id), Check.id <= entity.id)
            )
        else:
            statement = (
                select(func.count())
                .select_from(Card)
                .where(
                    Card.repeat_series_id == (entity.repeat_series_id or entity.id),
                    Card.id <= entity.id,
                )
            )
        live_id = await live_repeat_instance_id(session, entity)
        marks += REPEAT_MARKER.format(
            index=await session.scalar(statement),
            live="" if live_id is None else REPEAT_LIVE.format(live_id=live_id),
        )
    if entity.archived_at is not None:
        marks += ARCHIVE_MARKER
    return marks


async def live_repeat_instance_id(session: AsyncSession, entity: Card | Check) -> int | None:
    """The one open row of a repeat series, or None when the series has ended.

    Only the newest instance can be open, because closing one is what creates the next.
    """
    if isinstance(entity, Check):
        statement = (
            select(Check.id)
            .where(
                Check.series_id == (entity.series_id or entity.id),
                Check.outcome.is_(None),
            )
            .order_by(Check.id.desc())
        )
    else:
        statement = (
            select(Card.id)
            .where(
                Card.repeat_series_id == (entity.repeat_series_id or entity.id),
                Card.effective_stage.notin_([stage.value for stage in TERMINAL_STAGES]),
            )
            .order_by(Card.id.desc())
        )
    return await session.scalar(statement.limit(1))


async def archive_settled_items(session: AsyncSession) -> tuple[list[int], list[int]]:
    """Take what closed two Sprints ago off the screens, and report what left.

    A Sprint ending is the clock: nothing is archived while the workspace is in Planning,
    and whatever built up there leaves the moment the next Sprint ends.  Composed here
    because the clock is Planning's and the two archivers are Cards' and Checks'.
    """
    cutoff = await settled_cutoff(session)
    if cutoff is None:
        return [], []
    return (
        await archive_settled_cards(session, cutoff),
        await archive_settled_checks(session, cutoff),
    )


async def finish_sprint(session: AsyncSession, *, reason: str = "finished") -> Sprint:
    """End the Sprint and sweep what its ending settled."""
    sprint = await _finish_sprint(session, reason=reason)
    await archive_settled_items(session)
    return sprint


async def expire_due_sprint(session: AsyncSession, *, now: datetime | None = None) -> Sprint | None:
    """Close the active Sprint once local midnight has passed its planned end date.

    Unfinished Actions keep their stage: the Sprint ends, the plan does not evaporate.
    """
    if await sprint_is_due(session, now=now) is None:
        return None
    return await finish_sprint(session, reason="expired")
