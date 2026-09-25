"""The log of changes: one row per saved change to a Card, a Check, a Value, a Tag, a Request
or a Reminder, written by each feature that keeps one and kept after the item is gone
(AD-LOG-003). The Advisor and the heavy analyzer read it as `ai_log_events` (AD-LOG-004).
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, func, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tg_agent_shell.ai.sql import SqlView

from ..enums import ActorType
from .models import Base, UtcDateTime
from .workspace import require_workspace

CREATE = "create"
UPDATE = "update"
DELETE = "delete"

# How many rows of the log one read of the Advisor's brings back.
LOG_EVENTS_SHOWN = 20


class LogEvent(Base):
    """One saved change to one item.

    `item_id` is no foreign key, so the rows outlive the item, and `title` is its title or
    name at that moment, which is all a deleted item is still known by.
    """

    __tablename__ = "log_events"
    __table_args__ = (Index("ix_log_events_item", "item_type", "item_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_type: Mapped[str] = mapped_column(String(20))
    item_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(20))
    sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sprints.id", ondelete="SET NULL"))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now(), index=True)


def snapshot(item: Any) -> dict[str, Any]:
    """Every column of `item` that is loaded, as JSON.

    A column the database fills in, such as `created_at`, is not loaded until it is read
    again, and reading it here would be a query inside an async flush.
    """
    state = inspect(item)
    return {
        key: _plain(state.dict[key])
        for key in state.mapper.column_attrs.keys()
        if key in state.dict
    }


async def record_log_event(
    session: AsyncSession,
    item_type: str,
    item: Any,
    title: str,
    operation: str,
    actor: ActorType,
    before: dict[str, Any] | None = None,
) -> None:
    """Write one change to `item` in the same transaction as the change itself.

    `after` is the item as it stands now; a deletion keeps only what it was `before`.
    """
    workspace = await require_workspace(session)
    session.add(
        LogEvent(
            item_type=item_type,
            item_id=item.id,
            title=title,
            operation=operation,
            actor=actor.value,
            sprint_id=workspace.active_sprint_id,
            before=before,
            after=None if operation == DELETE else snapshot(item),
        )
    )


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date | time) else value


# SQLite hands a deleted item's id to the next item of its type, so a row up to that item's
# deletion carries no id: a link from it would open whatever holds that id now.
AI_LOG_EVENTS = SqlView(
    "ai_log_events",
    """SELECT e.id, e.item_type,
              CASE WHEN EXISTS (
                       SELECT 1 FROM log_events d
                        WHERE d.item_type = e.item_type AND d.item_id = e.item_id
                          AND d.operation = 'delete' AND d.id >= e.id)
                   THEN NULL ELSE e.item_id END AS item_id,
              e.title,
              CASE e.operation WHEN 'create' THEN 'created' WHEN 'delete' THEN 'deleted'
                   ELSE 'updated' END AS mode,
              e.operation, e.actor, e.sprint_id, substr(local_time(e.at), 1, 10) AS date
         FROM log_events e""",
    doc="""- `ai_log_events(id, item_type, item_id, title, mode, operation, actor, sprint_id, date)`
  - one row per saved change to one item; a higher `id` is a later change
  - `item_type` card | check | value | tag | request | reminder
  - `item_id` is the item's id, or NULL once the item was deleted
  - `title` is the item's title or name at that moment
  - `mode` created | updated | deleted
  - `operation` create | update | delete | edit_<field> | set_parent | move | done | archive | restore | link_<kind> | unlink_<kind> | passed | missed | reschedule
  - `actor` user_ui | ai — the user on a screen, or a proposal the user approved
  - `sprint_id` is the Sprint that was running then, or NULL
  - `date` is the local day, `YYYY-MM-DD`
  - archiving that happened on its own writes no row, so `archive` is always the user's own""",
)
