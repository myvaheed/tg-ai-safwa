"""One-off conversion of saved Card queries to typed Request results."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from safwa.bootstrap.modules import ALLOWED_VIEWS
from safwa.features.cards.model import Card
from safwa.features.saved_requests.api import explicit_order, request_items
from safwa.features.saved_requests.model import SavedRequest
from safwa.features.saved_requests.use_cases import update_saved_request
from safwa.security import held, unlock
from tg_agent_shell.ai.sql import validated_read
from tg_agent_shell.foundation.database import Database, DatabaseFile
from tg_agent_shell.foundation.errors import DomainError


async def upgrade_requests(
    sessions: async_sessionmaker[AsyncSession], *, apply: bool = False
) -> list[tuple[int, str, str]]:
    """Check every conversion first; commit all changes together only with apply."""
    changes: list[tuple[int, str, str]] = []
    async with sessions() as session:
        requests = list(await session.scalars(select(SavedRequest).order_by(SavedRequest.id)))
        for request in requests:
            try:
                statement, sources = validated_read(request.query_sql, ALLOWED_VIEWS)
                result = await session.execute(text(statement))
                if "item_type" in result.keys():
                    await request_items(session, statement, ALLOWED_VIEWS)
                    continue
                if "ai_cards" not in sources or "id" not in result.keys():
                    raise DomainError("Expected an old Card query returning id.")
                ids = list(dict.fromkeys(int(row["id"]) for row in result.mappings()))
                existing = set(await session.scalars(select(Card.id).where(Card.id.in_(ids))))
                converted = (
                    "SELECT 'card' AS item_type, CAST(request_rows.id AS INTEGER) AS id "
                    f"FROM ({statement}) AS request_rows"
                )
                # Keep the subquery's order visible to the Request screen's ORDER BY rule.
                if explicit_order(statement):
                    converted += " ORDER BY row_number() OVER ()"
                matches = await request_items(session, converted, ALLOWED_VIEWS)
                if [item.id for item in matches] != [item_id for item_id in ids if item_id in existing]:
                    raise DomainError("The converted query changed its results or their order.")
                changes.append((request.id, request.name, converted))
            except (ValueError, TypeError, SQLAlchemyError) as error:
                raise DomainError(f"Request #{request.id} ({request.name}): {error}") from error
        if apply:
            for request_id, _name, converted in changes:
                await update_saved_request(
                    session, request_id, query_sql=converted, views=ALLOWED_VIEWS
                )
            await session.commit()
    return changes


async def _run(file: DatabaseFile, *, apply: bool) -> list[tuple[int, str, str]]:
    database = Database(file)
    try:
        return await upgrade_requests(database.sessions, apply=apply)
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Existing encrypted Safwa database")
    parser.add_argument("--apply", action="store_true", help="Save all checked conversions")
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_file():
        parser.error(f"Database does not exist: {path}")
    try:
        with held(path):
            changes = asyncio.run(_run(unlock(path), apply=args.apply))
    except (DomainError, SQLAlchemyError) as error:
        raise SystemExit(f"{error} No Request changes were committed.") from error
    for request_id, name, _sql in changes:
        print(f"#{request_id} {name}")
    if args.apply:
        print(f"Updated {len(changes)} Requests.")
    else:
        print(f"Checked {len(changes)} conversions; no Requests changed. Add --apply to save.")


if __name__ == "__main__":
    main()
