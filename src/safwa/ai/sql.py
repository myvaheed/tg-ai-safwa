from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..constants import (
    DEFAULT_CELL_LIMIT,
    DEFAULT_CHAR_BUDGET,
    DEFAULT_COLUMN_LIMIT,
    DEFAULT_ROW_LIMIT,
    QUERY_TIMEOUT_SECONDS,
)


class UnsafeQueryError(ValueError):
    pass


ALLOWED_VIEWS = {
    "ai_cards",
    "ai_checks",
    "ai_tags",
    "ai_requests",
    "ai_values",
    "ai_current_sprint",
    "ai_current_sprint_metrics",
    "ai_card_events",
}
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|replace|alter|drop|create|pragma|attach|detach|vacuum|reindex|analyze)\b",
    re.IGNORECASE,
)


def validate_read_sql(sql: str) -> str:
    statement = sql.strip().rstrip(";").strip()
    if ";" in statement:
        raise UnsafeQueryError("Only one SQL statement is allowed")
    if not re.match(r"^(select|with)\b", statement, re.IGNORECASE):
        raise UnsafeQueryError("Only SELECT queries are allowed")
    if FORBIDDEN.search(statement):
        raise UnsafeQueryError("Unsafe SQL keyword")
    names = {
        match.group(1).casefold()
        for match in re.finditer(
            r"\b(?:from|join)\s+[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"`\]]?",
            statement,
            re.I,
        )
    }
    # A CTE may be recursive and may declare its columns, and both forms name a table
    # the FROM/JOIN scan below would otherwise report as an unavailable view.
    cte_names = {
        match.group(1).casefold()
        for match in re.finditer(
            r"(?:\bwith\s+(?:recursive\s+)?|,)\s*([A-Za-z_][A-Za-z0-9_]*)"
            r"\s*(?:\([^)]*\))?\s+as\s*\(",
            statement,
            re.I,
        )
    }
    disallowed = names - ALLOWED_VIEWS - cte_names
    if disallowed:
        raise UnsafeQueryError(
            "Query references unavailable views: " + ", ".join(sorted(disallowed))
        )
    return statement


def create_ai_views(connection) -> None:  # type: ignore[no-untyped-def]
    # Rebuild disposable read views so upgrades never retain an obsolete shape.
    for view_name in (
        "ai_tags",
        "ai_requests",
        "ai_cards",
        "ai_checks",
        "ai_values",
        "ai_current_sprint",
        "ai_current_sprint_metrics",
        "ai_card_events",
    ):
        connection.exec_driver_sql(f"DROP VIEW IF EXISTS {view_name}")
    # `card_search` was an FTS5 mirror of cards.title/note that nothing ever read: it was
    # absent from ALLOWED_VIEWS and from SYSTEM_PROMPT, so `validate_read_sql` rejected
    # every query against it.  Drop it and its write triggers from databases that still
    # carry them; Card lookup goes through `ai_cards` in `query_safwa`.
    for trigger_name in ("cards_search_insert", "cards_search_update", "cards_search_delete"):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger_name}")
    connection.exec_driver_sql("DROP TABLE IF EXISTS card_search")
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_tags AS
        SELECT id, name, description, created_at, updated_at FROM tags WHERE archived_at IS NULL"""
    )
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_requests AS
        SELECT id, name, description, query_sql, created_at, updated_at
        FROM saved_requests WHERE archived_at IS NULL"""
    )
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_values AS
        SELECT id, name, description, active, created_at, updated_at
        FROM "values" WHERE archived_at IS NULL"""
    )
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_cards AS
        SELECT c.id, c.title, c.note, c.kind, c.effective_stage AS stage, c.priority,
               c.hard_time, c.blocked, c.blocked_description,
               c.effort_points, c.repeatable, c.parent_id,
               (SELECT group_concat(cc.category, ',') FROM card_categories cc
                WHERE cc.card_id=c.id) AS categories,
               (SELECT group_concat(ce.energy_type, ',') FROM card_energy_types ce
                WHERE ce.card_id=c.id) AS energy_types,
               (SELECT group_concat(v.name, ',') FROM card_values cv
                JOIN "values" v ON v.id=cv.value_id WHERE cv.card_id=c.id) AS direct_values,
               (SELECT group_concat(t.name, ',') FROM card_tags ct
                JOIN tags t ON t.id=ct.tag_id WHERE ct.card_id=c.id) AS direct_tags,
               c.created_at, c.updated_at
        FROM cards c
        WHERE c.archived_at IS NULL"""
    )
    # `status` exposes the derived Pending state so a query never has to know that
    # Pending is stored as a null outcome.
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_checks AS
        SELECT k.id, k.card_id, k.title, k.note, k.repeatable,
               COALESCE(k.outcome, 'pending') AS status,
               k.resolved_at, k.series_id,
               (SELECT group_concat(v.name, ',') FROM check_values kv
                JOIN "values" v ON v.id=kv.value_id WHERE kv.check_id=k.id) AS direct_values,
               (SELECT group_concat(t.name, ',') FROM check_tags kt
                JOIN tags t ON t.id=kt.tag_id WHERE kt.check_id=k.id) AS direct_tags,
               k.created_at, k.updated_at
        FROM checks k
        WHERE k.archived_at IS NULL"""
    )
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_current_sprint AS
        SELECT s.id, s.number, s.planned_start_date, s.planned_end_date, s.actual_started_at
        FROM sprints s JOIN workspace w ON w.active_sprint_id = s.id"""
    )
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_current_sprint_metrics AS
        SELECT sc.sprint_id,
          SUM(CASE WHEN sc.scope_kind='initial' THEN sc.effort_snapshot ELSE 0 END) committed,
          SUM(CASE WHEN sc.scope_kind='added' THEN sc.effort_snapshot ELSE 0 END) added,
          SUM(CASE WHEN sc.removed_at IS NOT NULL THEN sc.effort_snapshot ELSE 0 END) removed,
          SUM(CASE WHEN sc.result='done' THEN sc.effort_snapshot ELSE 0 END) completed,
          SUM(CASE WHEN sc.result='cancelled' THEN sc.effort_snapshot ELSE 0 END) cancelled
        FROM sprint_commitments sc JOIN workspace w ON w.active_sprint_id=sc.sprint_id
        GROUP BY sc.sprint_id"""
    )
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_card_events AS
        SELECT id, card_id, sprint_id, actor, operation, created_at FROM card_events"""
    )


@dataclass(frozen=True)
class QueryOutcome:
    """Rows the model may use, plus a notice when something was left out."""

    rows: list[dict[str, Any]]
    notice: str | None = None

    def as_tool_result(self) -> list[dict[str, Any]]:
        """One JSON array; a trailing notice row appears only when a cap was hit."""
        return [*self.rows, {"notice": self.notice}] if self.notice else list(self.rows)


class ReadOnlyQueryRunner:
    """Run one validated read query under caps that keep a result promptable.

    A local model pays for every returned character, so an over-broad query is
    trimmed and told to narrow itself rather than silently filling the context.
    """

    def __init__(
        self,
        database_path: Path,
        *,
        row_limit: int = DEFAULT_ROW_LIMIT,
        char_budget: int = DEFAULT_CHAR_BUDGET,
        column_limit: int = DEFAULT_COLUMN_LIMIT,
        cell_limit: int = DEFAULT_CELL_LIMIT,
        timeout: float = QUERY_TIMEOUT_SECONDS,
    ) -> None:
        self.database_path = database_path.resolve()
        self.row_limit = row_limit
        self.char_budget = char_budget
        self.column_limit = column_limit
        self.cell_limit = cell_limit
        self.timeout = timeout

    def _trim(self, rows: list[sqlite3.Row]) -> tuple[list[dict[str, Any]], bool, bool]:
        result: list[dict[str, Any]] = []
        shortened = False
        over_budget = False
        size = 0
        for row in rows:
            cleaned: dict[str, Any] = {}
            for key, value in dict(row).items():
                if isinstance(value, str) and len(value) > self.cell_limit:
                    value = value[: self.cell_limit]
                    shortened = True
                cleaned[key] = value
            size += len(str(cleaned))
            if size > self.char_budget:
                over_budget = True
                break
            result.append(cleaned)
        return result, over_budget, shortened

    def _notice(self, shown: int, *, more_rows: bool, over_budget: bool, shortened: bool) -> str | None:
        notes: list[str] = []
        if over_budget and not shown:
            notes.append(
                f"The first row alone exceeded the {self.char_budget}-character result budget."
            )
        elif over_budget:
            notes.append(
                f"Only the first {shown} row(s) fit the {self.char_budget}-character result budget."
            )
        elif more_rows:
            notes.append(f"Only the first {shown} row(s) are shown; more rows match this query.")
        if shortened:
            notes.append(f"Long text values were cut to {self.cell_limit} characters.")
        if not notes:
            return None
        notes.append(
            "Narrow the query with a WHERE clause, fewer columns, or an aggregate before relying "
            "on this result as complete."
        )
        return " ".join(notes)

    def _run(self, sql: str) -> QueryOutcome:
        statement = validate_read_sql(sql)
        connection = sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)

        def authorizer(action, arg1, _arg2, _db, trigger):  # type: ignore[no-untyped-def]
            if action in {
                sqlite3.SQLITE_INSERT,
                sqlite3.SQLITE_UPDATE,
                sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_DROP_TABLE,
                sqlite3.SQLITE_ATTACH,
                sqlite3.SQLITE_PRAGMA,
            }:
                return sqlite3.SQLITE_DENY
            if (
                action == sqlite3.SQLITE_READ
                and arg1
                and arg1.casefold() not in ALLOWED_VIEWS
                and (not trigger or trigger.casefold() not in ALLOWED_VIEWS)
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorizer)
        deadline = time.monotonic() + self.timeout
        connection.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0,
            1_000,
        )
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.execute(statement)
            if cursor.description and len(cursor.description) > self.column_limit:
                raise UnsafeQueryError("Query returned too many columns")
            rows = cursor.fetchmany(self.row_limit + 1)
            more_rows = len(rows) > self.row_limit
            result, over_budget, shortened = self._trim(rows[: self.row_limit])
            return QueryOutcome(
                result,
                self._notice(
                    len(result),
                    more_rows=more_rows,
                    over_budget=over_budget,
                    shortened=shortened,
                ),
            )
        finally:
            connection.close()

    async def run(self, sql: str) -> QueryOutcome:
        return await asyncio.wait_for(asyncio.to_thread(self._run, sql), timeout=self.timeout)
