"""The read surface: one validated SELECT over the views the features publish.

The view catalogue is data, not a constant here. Each feature owns its `SqlView`, the
composition root collects them, and both the allowlist and `CREATE VIEW` come from that
one source — so a new view is never registered twice.

`query_data` is that surface as a tool, and every session that may read comes through
`read_query`: one door, and one wording for a read that was refused.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from collections.abc import Collection, Mapping, Sequence
from copy import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from llm_gateway import ToolCall

from .contracts import (
    QueryToolInput,
    ToolResultStatus,
    validation_error_summary,
)

logger = logging.getLogger(__name__)

# Sized for a local model: one result should inform a turn, not consume its context.
DEFAULT_ROW_LIMIT = 50
DEFAULT_CHAR_BUDGET = 12_000
DEFAULT_COLUMN_LIMIT = 20
DEFAULT_CELL_LIMIT = 2_000
QUERY_TIMEOUT_SECONDS = 2.0


class UnsafeQueryError(ValueError):
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
# A string literal or a quoted name, with its doubled-quote escape: what a keyword hides in.
QUOTED = r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[(?:\]\]|[^\]])*\]"
SQL_TOKEN = re.compile(
    r"(?P<comment>--|/\*)"
    rf"|(?P<quoted>{QUOTED})"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<punct>[(),])"
)
_QUOTED = re.compile(QUOTED)


def _unquoted(token: str) -> str:
    if token[0] == "[":
        return token[1:-1].replace("]]", "]")
    if token[0] in "'\"`":
        return token[1:-1].replace(token[0] * 2, token[0])
    return token


@dataclass(slots=True)
class _Scope:
    """One parenthesised level of a statement, and the WITH clause declared at it.

    A CTE is readable inside the query that declared it and nowhere else, so its name lives
    on the level that declared it and goes out of reach when that level closes. `stage` is
    where that level's WITH clause has got to: a name, its AS, then its body.
    """

    ctes: set[str] = field(default_factory=set)
    stage: str = ""
    pending: str = ""
    in_from: bool = False
    cte_body: bool = False


def scan_statement(statement: str, views: Collection[str]) -> set[str]:
    """Every token-level rule, and every table source resolved in the scope that names it.
    Returns the views the statement reads, for a caller with a rule about which ones.

    One pass, because each rule reads the same tokens: a keyword only counts outside a
    string literal, and a table source can be hidden behind a comment, a parenthesized bare
    name or SQLite's legacy comma-separated list. Splitting these into separate regular
    expressions is what let a query name a base table the FROM/JOIN allowlist never saw —
    the last of them read `'WITH private_rows AS ('` out of a string literal and took it
    for a declaration. A CTE is a declaration position, not a shape found anywhere in the
    text: the name after WITH, or after a comma that follows a finished CTE body. It is
    also a declaration *somewhere*, which one flat set of names could not say — a WITH
    inside a subquery covered a base table of the same name in the query around it.

    Sources are resolved after the walk, against the levels that were open where each one
    stood: SQLite matches a name against every CTE of an enclosing WITH clause, whichever
    side of it the name was written on.
    """
    scopes = [_Scope()]
    sources: list[tuple[str, tuple[_Scope, ...]]] = []
    expect_name = False  # the previous token was FROM or JOIN
    expect_select = False  # ... and an opening parenthesis followed it
    for match in SQL_TOKEN.finditer(statement):
        token = match.group()
        if match.lastgroup == "comment":
            raise UnsafeQueryError("SQL comments are not allowed")
        scope = scopes[-1]
        word = match.lastgroup == "word"
        named = word or match.lastgroup == "quoted"
        lowered = token.casefold() if word else ""
        if scope.stage == "name" and lowered != "recursive":
            scope.stage, scope.pending = ("as", _unquoted(token).casefold()) if named else ("", "")
        elif scope.stage == "as" and token != "(":
            # A parenthesis here is the optional column list, and it opens a level of its own.
            scope.stage = "body" if lowered == "as" else ""
        elif scope.stage == "body" and token != "(":
            scope.stage = ""
        elif scope.stage == "closed":
            scope.stage = "name" if token == "," else ""
        elif scope.stage == "" and lowered == "with":
            scope.stage = "name"
        if token == "(":
            expect_name, expect_select = False, expect_name or expect_select
            if scope.stage == "body":
                # Declared from the first token of its own body, which is what RECURSIVE reads.
                scope.ctes.add(scope.pending)
            scopes.append(_Scope(cte_body=scope.stage == "body"))
            continue
        if expect_select and (not word or lowered not in {"select", "with"}):
            raise UnsafeQueryError("Name a view directly after FROM or JOIN")
        expect_select = False
        if token == ")":
            if len(scopes) > 1 and scopes.pop().cte_body:
                scopes[-1].stage = "closed"
        elif token == ",":
            if scope.in_from:
                raise UnsafeQueryError("Use JOIN instead of a comma-separated table list")
        elif expect_name:
            sources.append((_unquoted(token).casefold(), tuple(scopes)))
            expect_name = False
        elif word:
            if lowered in FORBIDDEN:
                raise UnsafeQueryError("Unsafe SQL keyword")
            if lowered in {"from", "join"}:
                expect_name, scope.in_from = True, True
            elif lowered in FROM_END:
                scope.in_from = False
    unresolved = sorted(
        {
            name
            for name, open_scopes in sources
            if name not in views and not any(name in level.ctes for level in open_scopes)
        }
    )
    if unresolved:
        raise UnsafeQueryError(
            "Query references unavailable views: " + ", ".join(unresolved)
        )
    return {name for name, _ in sources if name in views}


# What a read that is more than one flat scan of one view always contains. A bare
# aggregate is deliberately not here: `SELECT count(*) FROM ai_cards WHERE stage = 'today'`
# is a lookup, and offering help for it would fire on most turns. Counting is hard once it
# is grouped or joined, and both of those are caught.
COMPLEX_READ = re.compile(
    r"\b(join|group\s+by|having|union|intersect|except|with)\b|\bover\s*\(|\(\s*select\b",
    re.IGNORECASE,
)


def is_complex_read(sql: str) -> bool:
    """Whether one read goes past a single flat scan, and so past what a small model writes
    well. Read with every literal and quoted name blanked: a title 'Talk with Alice' is not
    a WITH clause. A comment is refused by `validated_read` whatever it says."""
    return bool(COMPLEX_READ.search(_QUOTED.sub("''", sql)))


def validated_read(sql: str, views: Collection[str]) -> tuple[str, set[str]]:
    """One safe read: the statement to run, and the views it reads.

    A CTE may be recursive and may declare its columns, and both forms name a table the
    FROM/JOIN scan would otherwise report as an unavailable view.
    """
    statement = sql.strip().rstrip(";").strip()
    if ";" in statement:
        raise UnsafeQueryError("Only one SQL statement is allowed")
    if not re.match(r"^(select|with)\b", statement, re.IGNORECASE):
        raise UnsafeQueryError("Only SELECT queries are allowed")
    return statement, scan_statement(statement, views)


def create_ai_views(connection, views: Sequence[SqlView]) -> None:  # type: ignore[no-untyped-def]
    """Rebuild the disposable read views, so an upgrade never keeps an obsolete shape."""
    for view in views:
        connection.exec_driver_sql(f"DROP VIEW IF EXISTS {view.name}")
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
        self.row_limits: Mapping[str, int] = {}
        self.char_budget = char_budget
        self.column_limit = column_limit
        self.cell_limit = cell_limit
        self.timeout = timeout

    def scoped(
        self, views: Collection[str], *, row_limits: Mapping[str, int] | None = None
    ) -> ReadOnlyQueryRunner:
        """The same runner for one reader, over the views that reader declared.

        The list a reader's prompt describes and the list its reads may name are one
        declaration, so a view it is never told about is one it cannot reach by guessing
        the name. Every cap travels along, because a reader's scope is the only difference.
        `row_limits` cuts a read of one of its views shorter than the rest.
        """
        narrowed = frozenset(views)
        unknown = narrowed - self.views
        if unknown:
            raise RuntimeError(f"A reader asks for views no feature publishes: {sorted(unknown)}")
        reader = copy(self)
        reader.views = narrowed
        reader.row_limits = dict(row_limits or {})
        return reader

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
        statement, read = validated_read(sql, self.views)
        row_limit = min(
            [self.row_limit, *(self.row_limits[name] for name in read if name in self.row_limits)]
        )
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
            # A view flattened into a rowid scan reports its base table with no column and
            # no view attribution — `('cards', '', None)`, which is the same callback a
            # bare `count(*)` over a forbidden table makes. The authorizer cannot tell the
            # two apart, so `scan_statement` owns every table source by name and this
            # independently refuses every attributed base column.
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
            rows = cursor.fetchmany(row_limit + 1)
            more_rows = len(rows) > row_limit
            result, over_budget, shortened = self._trim(rows[:row_limit])
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


@dataclass
class QueryRead:
    """One `query_data` call: the SQL it asked for, and the rows the model reads back.

    `sql` is empty when the call never named one, which is how a caller tells a refused
    SELECT from a call whose arguments were not a query at all.
    """

    sql: str
    rows: list[dict[str, Any]]
    succeeded: bool = True


async def read_query(runner: ReadOnlyQueryRunner, call: ToolCall) -> QueryRead:
    """Answer one `query_data` call. Every session that may read comes through here.

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
            logger.info("AI TOOL query_data capped: %s", outcome.notice)
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
                        "Fix only this SELECT and call query_data again. One read-only "
                        "SELECT or WITH … SELECT over the ai_* views, no other statement. "
                        "This failure changed nothing: every step of the request already "
                        "resolved above still stands, so do not restart the request."
                    ),
                    "retryable": True,
                }
            ],
            succeeded=False,
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
                        'FROM ai_cards LIMIT 20"}, and call query_data again.'
                    ),
                    "retryable": True,
                }
            ],
            succeeded=False,
        )
