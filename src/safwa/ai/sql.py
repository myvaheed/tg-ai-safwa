"""The read surface: one validated SELECT over the views the features publish.

The view catalogue is data, not a constant here. Each feature owns its `SqlView`, the
composition root collects them, and both the allowlist and `CREATE VIEW` come from that
one source — so a new view is never registered twice.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..constants import (
    DEFAULT_CELL_LIMIT,
    DEFAULT_CHAR_BUDGET,
    DEFAULT_COLUMN_LIMIT,
    DEFAULT_ROW_LIMIT,
    QUERY_TIMEOUT_SECONDS,
    REPEAT_LIVE,
    REPEAT_MARKER,
)

# The marks `domain.title_marks` renders, as SQLite format strings: one wording, so a row
# reads the same whether the model queried it or the owner tapped a citation.
MARKER_FORMAT = REPEAT_MARKER.replace("{index}", "%d").replace("{live}", "%s")
LIVE_FORMAT = REPEAT_LIVE.replace("{live_id}", "%d")


class UnsafeQueryError(ValueError):
    pass


class RequestQueryError(ValueError):
    pass


def local_time(stored: str | None, tz: ZoneInfo) -> str | None:
    """A stored UTC timestamp as the owner's local wall clock.

    SQLite has no timezone database, so a view that wants local time asks for this
    function; a fixed offset written into the view SQL would be an hour wrong for half of
    every daylight-saving year.
    """
    if not stored:
        return None
    return f"{datetime.fromisoformat(stored).replace(tzinfo=UTC).astimezone(tz):%Y-%m-%d %H:%M}"


@dataclass(frozen=True, slots=True)
class SqlView:
    """One `ai_*` view: the name the model sees, the SELECT that builds it, and the block
    a reader is given about it.

    The block lives beside the SELECT because the two say the same thing to two audiences:
    a column that changes shape and a column that changes meaning are then one edit.
    """

    name: str
    sql: str
    doc: str = ""


def view_catalogue(views: Collection[SqlView], names: Sequence[str]) -> str:
    """The blocks of the named views, in the order the reader asked for them.

    A reader is scoped by the list it is given: a view it is never told about is a view it
    never queries, which is the only scoping a shared allowlist leaves available.
    """
    by_name = {view.name: view for view in views}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise RuntimeError(f"A reader asks for views no feature publishes: {unknown}")
    silent = [name for name in names if not by_name[name].doc.strip()]
    if silent:
        raise RuntimeError(f"A reader is offered views with no documentation: {silent}")
    return "\n".join(by_name[name].doc.strip("\n") for name in names)


FORBIDDEN = re.compile(
    r"\b(insert|update|delete|replace|alter|drop|create|pragma|attach|detach|vacuum|reindex|analyze)\b",
    re.IGNORECASE,
)


# What a read that is more than one flat scan of one view always contains. A bare
# aggregate is deliberately not here: `SELECT count(*) FROM ai_cards WHERE stage = 'today'`
# is a lookup, and offering help for it would fire on most turns. Counting is hard once it
# is grouped or joined, and both of those are caught.
COMPLEX_READ = re.compile(
    r"\b(join|group\s+by|having|union|intersect|except|with)\b|\bover\s*\(|\(\s*select\b",
    re.IGNORECASE,
)


def is_complex_read(sql: str) -> bool:
    """Whether one read goes past a single flat scan, and so past what a small model writes well."""
    return bool(COMPLEX_READ.search(sql))


def validate_read_sql(sql: str, views: Collection[str]) -> str:
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
    disallowed = names - set(views) - cte_names
    if disallowed:
        raise UnsafeQueryError(
            "Query references unavailable views: " + ", ".join(sorted(disallowed))
        )
    return statement


def normalize_request_sql(raw: str, views: Collection[str]) -> str:
    """Validate a read query that has to come back with Card ids.

    Two callers, and neither owns it: a saved Request's SQL, and the `parent_query` a Card
    proposal may resolve its parent with. Both need the shared read validator plus the two
    rules that make the result usable as Card ids.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise RequestQueryError("Request SQL is required")
    try:
        statement = validate_read_sql(raw, views)
    except UnsafeQueryError as error:
        raise RequestQueryError(str(error)) from error
    if "ai_cards" not in statement.casefold():
        raise RequestQueryError("Request SQL must query ai_cards and return Card ids")
    if not re.search(
        r"\bselect\s+(?:distinct\s+)?(?:[a-z_][a-z0-9_]*\.)?id(?:\s+as\s+id)?\b",
        statement,
        re.IGNORECASE,
    ):
        raise RequestQueryError("Request SQL must return a column named id")
    return statement


def create_ai_views(connection, views: Sequence[SqlView]) -> None:  # type: ignore[no-untyped-def]
    """Rebuild the disposable read views, so an upgrade never keeps an obsolete shape."""
    for view in views:
        connection.exec_driver_sql(f"DROP VIEW IF EXISTS {view.name}")
    # Card lookup goes through `ai_cards`, so drop the `card_search` FTS5 table and its
    # write triggers from databases that still carry them.
    for trigger_name in ("cards_search_insert", "cards_search_update", "cards_search_delete"):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger_name}")
    connection.exec_driver_sql("DROP TABLE IF EXISTS card_search")
    for view in views:
        connection.exec_driver_sql(f"CREATE VIEW IF NOT EXISTS {view.name} AS {view.sql}")


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
        views: Collection[str],
        *,
        row_limit: int = DEFAULT_ROW_LIMIT,
        char_budget: int = DEFAULT_CHAR_BUDGET,
        column_limit: int = DEFAULT_COLUMN_LIMIT,
        cell_limit: int = DEFAULT_CELL_LIMIT,
        timeout: float = QUERY_TIMEOUT_SECONDS,
        timezone: str = "UTC",
    ) -> None:
        self.database_path = database_path.resolve()
        self.views = frozenset(views)
        self.tz = ZoneInfo(timezone)
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
        statement = validate_read_sql(sql, self.views)
        connection = sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)

        def authorizer(action, arg1, column, _db, trigger):  # type: ignore[no-untyped-def]
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
            # A read with no column name discloses no column. SQLite reports one when a
            # view is flattened into a scan that needs none — `SELECT count(*)` over any
            # view, or `SELECT id` where the id is the rowid — and it names no view to
            # attribute it to. Denying it would refuse those queries outright, and the
            # statement validator has already refused every FROM that is not a view.
            if (
                action == sqlite3.SQLITE_READ
                and arg1
                and column
                and arg1.casefold() not in self.views
                and (not trigger or trigger.casefold() not in self.views)
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.create_function(
            "local_time", 1, lambda stored: local_time(stored, self.tz), deterministic=True
        )
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
