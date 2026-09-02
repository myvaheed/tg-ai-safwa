"""The read surface: one validated SELECT over the views the features publish.

The view catalogue is data, not a constant here. Each feature owns its `SqlView`, the
composition root collects them, and both the allowlist and `CREATE VIEW` come from that
one source — so a new view is never registered twice.

`query_safwa` is that surface as a tool, and every session that may read comes through
`read_query`: one door, and one wording for a read that was refused.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from llm_gateway import ToolCall

from ..constants import (
    DEFAULT_CELL_LIMIT,
    DEFAULT_CHAR_BUDGET,
    DEFAULT_COLUMN_LIMIT,
    DEFAULT_ROW_LIMIT,
    QUERY_TIMEOUT_SECONDS,
    REPEAT_LIVE,
    REPEAT_MARKER,
)
from .contracts import (
    QueryToolInput,
    ToolResultStatus,
    tool_json_schema,
    validation_error_summary,
)

logger = logging.getLogger(__name__)

# The marks `foundation.marks.title_marks` renders, as SQLite format strings: one wording, so a row
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


FORBIDDEN = frozenset(
    {
        "insert", "update", "delete", "replace", "alter", "drop",
        "create", "pragma", "attach", "detach", "vacuum", "reindex", "analyze",
    }
)
# What ends a FROM clause, so a comma past it is a list of columns rather than of tables.
FROM_END = frozenset(
    {"where", "group", "having", "order", "limit", "union", "intersect", "except", "window"}
)
SQL_TOKEN = re.compile(
    r"(?P<comment>--|/\*)"
    r"|(?P<quoted>'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[(?:\]\]|[^\]])*\])"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<punct>[(),])"
)


def _unquoted(token: str) -> str:
    if token[0] == "[":
        return token[1:-1].replace("]]", "]")
    if token[0] in "'\"`":
        return token[1:-1].replace(token[0] * 2, token[0])
    return token


def scan_statement(statement: str) -> set[str]:
    """Every token-level rule, and the table names the allowlist below is checked against.

    One pass, because each rule reads the same tokens: a keyword only counts outside a
    string literal, and a table source can be hidden behind a comment, a parenthesized bare
    name or SQLite's legacy comma-separated list. Splitting these into separate regular
    expressions is what let a query name a base table the FROM/JOIN allowlist never saw.
    """
    names: set[str] = set()
    depth = 0
    from_depths: set[int] = set()
    expect_name = False  # the previous token was FROM or JOIN
    expect_select = False  # ... and an opening parenthesis followed it
    for match in SQL_TOKEN.finditer(statement):
        token = match.group()
        if match.lastgroup == "comment":
            raise UnsafeQueryError("SQL comments are not allowed")
        if token == "(":
            expect_name, expect_select = False, expect_name or expect_select
            depth += 1
            continue
        if expect_select and (
            match.lastgroup != "word" or token.casefold() not in {"select", "with"}
        ):
            raise UnsafeQueryError("Name a view directly after FROM or JOIN")
        expect_select = False
        if token == ")":
            from_depths.discard(depth)
            depth = max(0, depth - 1)
        elif token == ",":
            if depth in from_depths:
                raise UnsafeQueryError("Use JOIN instead of a comma-separated table list")
        elif expect_name:
            names.add(_unquoted(token).casefold())
            expect_name = False
        elif match.lastgroup == "word":
            lowered = token.casefold()
            if lowered in FORBIDDEN:
                raise UnsafeQueryError("Unsafe SQL keyword")
            if lowered in {"from", "join"}:
                expect_name = True
                from_depths.add(depth)
            elif lowered in FROM_END:
                from_depths.discard(depth)
    return names


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
    names = scan_statement(statement)
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
            # SQLite sometimes reports an empty column for a view flattened into a
            # rowid scan. The statement validator therefore owns columnless table-source
            # reads; the authorizer independently refuses every attributed base column.
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


QUERY_SAFWA_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "query_safwa",
        "description": (
            "Read Safwa's current data with one read-only SELECT over the ai_* views listed "
            "in your instructions. Use it before you answer or propose anything."
        ),
        "parameters": tool_json_schema(QueryToolInput),
    },
}


@dataclass
class QueryRead:
    """One `query_safwa` call: the SQL it asked for, and the rows the model reads back.

    `sql` is empty when the call never named one, which is how a caller tells a refused
    SELECT from a call whose arguments were not a query at all.
    """

    sql: str
    rows: list[dict[str, Any]]


async def read_query(runner: ReadOnlyQueryRunner, call: ToolCall) -> QueryRead:
    """Answer one `query_safwa` call. Every session that may read comes through here.

    A rejected or broken read is the model's to repair, so it comes back as a retryable
    tool result rather than an exception: raising would end the whole request, including
    any change queued alongside it.
    """
    sql = ""
    try:
        query = QueryToolInput.model_validate(json.loads(call.arguments_json))
        sql = query.sql
        outcome = await runner.run(sql)
        if outcome.notice:
            logger.info("AI TOOL query_safwa capped: %s", outcome.notice)
        return QueryRead(sql, outcome.as_tool_result())
    # ``UnsafeQueryError`` is a ``ValueError``, so it has to be caught before the
    # argument-shape clause or a rejected SELECT is reported as a bad argument and the
    # model rewrites the call instead of the query.
    except (UnsafeQueryError, sqlite3.Error, TimeoutError, OSError) as error:
        return QueryRead(
            sql,
            [
                {
                    "status": ToolResultStatus.ERROR.value,
                    "code": "unsafe_query" if isinstance(error, UnsafeQueryError) else "query_failed",
                    "error": str(error),
                    "hint": (
                        "Fix only this SELECT and call query_safwa again. One read-only "
                        "SELECT or WITH … SELECT over the ai_* views, no other statement. "
                        "This failure changed nothing: every step of the request already "
                        "resolved above still stands, so do not restart the request."
                    ),
                    "retryable": True,
                }
            ],
        )
    except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
        return QueryRead(
            "",
            [
                {
                    "status": ToolResultStatus.ERROR.value,
                    "code": "invalid_arguments",
                    "error": (
                        validation_error_summary(error)
                        if isinstance(error, ValidationError)
                        else str(error)
                    ),
                    "hint": (
                        'Send exactly one string argument, e.g. {"sql": "SELECT id, title '
                        'FROM ai_cards LIMIT 20"}, and call query_safwa again.'
                    ),
                    "retryable": True,
                }
            ],
        )
