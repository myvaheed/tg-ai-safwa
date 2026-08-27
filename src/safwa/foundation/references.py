"""Resolving named and numeric relationships from an AI proposal payload."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def listed(value: Any) -> list[Any]:
    """Accept either one reference or a list of them from a proposal payload."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@dataclass(frozen=True)
class ReferenceSpec:
    """Where one relationship lives in a payload and how it is written.

    ``key`` is the whole naming: a payload offers ``value_id``, ``value_ids`` and
    ``value_query``, and ``value_id`` is also the link table's own column name, so the same
    spec addresses the payload, the lookup and the junction row. ``owner`` is the other half
    of that row: a Card carries Values, Tags and Checks, and a Check carries Values.
    """

    key: str
    label: str
    model: type[Any]
    link_model: type[Any]
    toggle: Callable[..., Awaitable[bool]]
    # A Check is named by `title`, so the column a query_key resolves against varies.
    name_attr: str = "name"
    # What does the linking, for a screen that counts what carries this item.
    owner: str = "Card"
    # Only a Check is ever archived; a Value and a Tag are deleted instead, so there is no
    # archived one for a link to be refused against.
    archivable: bool = False

    @property
    def singular_key(self) -> str:
        return f"{self.key}_id"

    @property
    def plural_key(self) -> str:
        return f"{self.key}_ids"

    @property
    def query_key(self) -> str:
        return f"{self.key}_query"

    @property
    def owner_key(self) -> str:
        return f"{self.owner.casefold()}_id"

    def is_live(self, entity: Any) -> bool:
        return not (self.archivable and entity.archived_at is not None)

    @property
    def live_filters(self) -> tuple[Any, ...]:
        return (self.model.archived_at.is_(None),) if self.archivable else ()

    def mentioned_in(self, values: dict[str, Any]) -> bool:
        return bool({self.singular_key, self.plural_key, self.query_key} & values.keys())

    def link_key(self, owner_id: int, entity_id: int) -> dict[str, int]:
        return {self.owner_key: owner_id, self.singular_key: entity_id}

    @property
    def link_column(self) -> Any:
        return self.link_model.__table__.c[self.singular_key]

    @property
    def name_column(self) -> Any:
        return getattr(self.model, self.name_attr)


@dataclass(frozen=True)
class ResolvedReferences:
    """What a payload's references point at, and what could not be resolved."""

    ids: set[int]
    unknown_ids: tuple[int, ...] = ()
    missing: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()
    blank: bool = False

    @property
    def unresolved(self) -> tuple[str, ...]:
        return (*self.missing, *self.ambiguous)


async def resolve_references(
    session: AsyncSession, spec: ReferenceSpec, values: dict[str, Any]
) -> ResolvedReferences:
    """Resolve one relationship's IDs and exact names against committed data."""
    ids: set[int] = set()
    unknown_ids: list[int] = []
    for raw_id in [*listed(values.get(spec.singular_key)), *listed(values.get(spec.plural_key))]:
        entity_id = int(raw_id)
        entity = await session.get(spec.model, entity_id)
        if entity is None or not spec.is_live(entity):
            unknown_ids.append(entity_id)
        else:
            ids.add(entity_id)

    missing: list[str] = []
    ambiguous: list[str] = []
    blank = False
    for raw_name in listed(values.get(spec.query_key)):
        name = str(raw_name).strip()
        if not name:
            blank = True
            continue
        matches = list(
            await session.scalars(
                select(spec.model).where(
                    spec.name_column.collate("NOCASE") == name,
                    *spec.live_filters,
                )
            )
        )
        if len(matches) == 1:
            ids.add(matches[0].id)
        elif matches:
            ambiguous.append(name)
        else:
            missing.append(name)
    return ResolvedReferences(ids, tuple(unknown_ids), tuple(missing), tuple(ambiguous), blank)
