from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from pathlib import Path
from typing import Any


class UnsafeQueryError(ValueError):
    pass


ALLOWED_VIEWS = {
    "ai_cards",
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
        for match in re.finditer(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", statement, re.I)
    }
    cte_names = {
        match.group(1).casefold()
        for match in re.finditer(
            r"(?:\bwith|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", statement, re.I
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
        "ai_boards",
        "ai_tags",
        "ai_requests",
        "ai_cards",
        "ai_values",
        "ai_current_sprint",
        "ai_current_sprint_metrics",
        "ai_card_events",
    ):
        connection.exec_driver_sql(f"DROP VIEW IF EXISTS {view_name}")
    connection.exec_driver_sql(
        "CREATE VIRTUAL TABLE IF NOT EXISTS card_search USING fts5(card_id UNINDEXED, title, note)"
    )
    connection.exec_driver_sql(
        "INSERT INTO card_search(card_id, title, note) "
        "SELECT id, title, note FROM cards WHERE id NOT IN (SELECT card_id FROM card_search)"
    )
    connection.exec_driver_sql(
        """CREATE TRIGGER IF NOT EXISTS cards_search_insert AFTER INSERT ON cards BEGIN
        INSERT INTO card_search(card_id, title, note) VALUES (new.id, new.title, new.note); END"""
    )
    connection.exec_driver_sql(
        """CREATE TRIGGER IF NOT EXISTS cards_search_update AFTER UPDATE OF title, note ON cards BEGIN
        DELETE FROM card_search WHERE card_id=old.id;
        INSERT INTO card_search(card_id, title, note) VALUES (new.id, new.title, new.note); END"""
    )
    connection.exec_driver_sql(
        """CREATE TRIGGER IF NOT EXISTS cards_search_delete AFTER DELETE ON cards BEGIN
        DELETE FROM card_search WHERE card_id=old.id; END"""
    )
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_tags AS
        SELECT id, name, description FROM tags WHERE archived_at IS NULL"""
    )
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_requests AS
        SELECT id, name, description, filter_spec FROM saved_requests WHERE archived_at IS NULL"""
    )
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_values AS
        SELECT id, name, description, active FROM "values" WHERE archived_at IS NULL"""
    )
    connection.exec_driver_sql(
        """CREATE VIEW IF NOT EXISTS ai_cards AS
        SELECT c.id, c.title, c.note, c.kind, c.effective_stage AS stage, c.priority,
               c.hard_time, c.effort_points, c.repeatable, c.parent_id,
               (SELECT group_concat(cc.category, ',') FROM card_categories cc
                WHERE cc.card_id=c.id) AS categories,
               (SELECT group_concat(ce.energy_type, ',') FROM card_energy_types ce
                WHERE ce.card_id=c.id) AS energy_types,
               (SELECT group_concat(v.name, ',') FROM card_values cv
                JOIN "values" v ON v.id=cv.value_id WHERE cv.card_id=c.id) AS direct_values,
               (SELECT group_concat(t.name, ',') FROM card_tags ct
                JOIN tags t ON t.id=ct.tag_id WHERE ct.card_id=c.id) AS direct_tags,
               (SELECT group_concat(cd.blocker_card_id, ',') FROM card_dependencies cd
                WHERE cd.blocked_card_id=c.id) AS blocker_ids
        FROM cards c
        WHERE c.archived_at IS NULL"""
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


class ReadOnlyQueryRunner:
    def __init__(self, database_path: Path, *, row_limit: int = 100, timeout: float = 2.0) -> None:
        self.database_path = database_path.resolve()
        self.row_limit = row_limit
        self.timeout = timeout

    def _run(self, sql: str) -> list[dict[str, Any]]:
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
            if cursor.description and len(cursor.description) > 20:
                raise UnsafeQueryError("Query returned too many columns")
            rows = cursor.fetchmany(self.row_limit + 1)
            if len(rows) > self.row_limit:
                rows = rows[: self.row_limit]
            result: list[dict[str, Any]] = []
            size = 0
            for row in rows:
                cleaned = {
                    key: (value[:2_000] if isinstance(value, str) else value)
                    for key, value in dict(row).items()
                }
                size += len(str(cleaned))
                if size > 50_000:
                    break
                result.append(cleaned)
            return result
        finally:
            connection.close()

    async def run(self, sql: str) -> list[dict[str, Any]]:
        return await asyncio.wait_for(asyncio.to_thread(self._run, sql), timeout=self.timeout)
