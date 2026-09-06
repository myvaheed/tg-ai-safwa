"""What a feature contributes when it owns an entity the model may change.

One registration, three responsibilities, and they are three different layers:

- `ProposalHandler` checks a change against live data and applies an approved one — the
  application side, which calls the same domain functions the manual UI calls;
- `MutationToolSpec` is what the model sees — the schema, the one-line description and
  the conversion into a neutral `AgentChange`;
- `ProposalPresenter` is how the change reads to the owner — the receipt lines and the
  review screen.

Generic proposal code holds the orchestration: the workspace and its revision, the batch
and its open reviews, the optimistic lock. Loading the entity, `archived_at`, the closed
repeat and `target_not_found` belong to the feature that owns the entity.
"""

from __future__ import annotations

import html
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import LlmProvider

from ..ai.contracts import (
    AgentChange,
    MutationToolSpec,
    ToolResultStatus,
)
from ..ai.sql import ReadOnlyQueryRunner
from ..foundation.errors import DomainError
from ..foundation.references import ReferenceSpec, resolve_references
from .model import ChangeAction, ProposalChange

__all__ = ["MutationToolSpec"]


class ToolPreparationError(DomainError):
    """A model-visible error for one mutation call, not for the whole agent turn."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "status": ToolResultStatus.ERROR.value,
            "code": self.code,
            "error": str(self),
            "hint": self.hint,
            "retryable": True,
        }


@dataclass(frozen=True)
class PreparedChange:
    """What a proposal needs: the resolved values and the version they assume."""

    values: dict[str, Any]
    expected_version: int | None


@dataclass(frozen=True, slots=True)
class World:
    """The state of the surrounding application one proposal is made against.

    `revision` moves whenever anything the model may propose against changes, and a
    proposal is saveable only while it still matches. The application binds
    `ProposalRegistry.world` to whatever holds these; nothing here knows what that is.
    """

    revision: int
    timezone: str


WorldReader = Callable[[AsyncSession], Awaitable[World]]


@dataclass(frozen=True, slots=True)
class PreparationContext:
    """Everything preparation may read. It writes nothing."""

    session: AsyncSession
    world: World
    provider: LlmProvider
    query_runner: ReadOnlyQueryRunner
    # The read surface a Request's SQL may reference.
    views: frozenset[str]


@dataclass(frozen=True, slots=True)
class ApplyContext:
    """Everything an approved change may use while it is written."""

    session: AsyncSession
    views: frozenset[str]
    allow_destructive: bool = False


class ProposalHandler(Protocol):
    """How one entity's proposal is checked, applied and re-versioned."""

    entity: str
    # The model a requeued proposal re-reads its expected version from, or None to leave
    # the recorded version alone.
    version_model: type[Any] | None
    # Actions this feature will not let a single Save carry out. The review screen asks
    # again for these, and `ApplyContext.allow_destructive` is what that answer sets.
    # Optional: a handler that declares nothing has nothing a second press could add.
    destructive_actions: frozenset[ChangeAction]

    async def prepare(
        self, context: PreparationContext, change: AgentChange
    ) -> PreparedChange:
        """Check the change against live data. Raises `ToolPreparationError` with a hint."""

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        """Call the same domain operations the manual UI calls. Returns the ids touched."""


async def require_target(
    context: PreparationContext, change: AgentChange, model: type[Any]
) -> tuple[Any | None, int | None]:
    """Load the change's target and the version it assumes.

    A change that names an id it cannot reach is one retryable tool error, not the end of
    the turn. The wording is shared because the recovery is: find the id, or say so.
    """
    entity = await context.session.get(model, change.id) if change.id else None
    expected_version = entity.version if entity is not None else None
    if change.id is not None and entity is None:
        raise ToolPreparationError(
            "target_not_found",
            f"{change.entity.title()} #{change.id} does not exist.",
            "Find the current numeric ID with query_data and retry. If nothing matches, say so "
            "instead of proposing again.",
        )
    if entity is not None and getattr(entity, "archived_at", None) is not None:
        raise ToolPreparationError(
            "target_archived",
            f"{change.entity.title()} #{change.id} is archived.",
            f"An archived item is not changed automatically. Do not propose this again. End your "
            f"answer with one line naming it: [title]({change.entity}:{change.id}). The owner "
            f"opens it and changes it by hand.",
        )
    return entity, expected_version


def entity_change(entity: str) -> Callable[[BaseModel], AgentChange]:
    """The ordinary conversion: `mode` is the action, and the tool names the entity."""

    def convert(call: BaseModel) -> AgentChange:
        values = call.model_dump(exclude_unset=True)
        values.pop("mode", None)
        return AgentChange(
            entity=entity,
            action=str(call.mode),
            id=values.pop("id", None),
            values=values,
        )

    return convert


@dataclass(frozen=True, slots=True)
class ProposalScreen:
    """The review screen of a single-change proposal, before any wording is joined."""

    mode: str
    item: str
    blocks: tuple[str, ...] = ()
    diffs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposalDescription:
    """One proposal in owner-facing words: a headline plus its `Label: value` lines."""

    summary: str
    fields: list[str] = field(default_factory=list)


class ProposalPresenter(Protocol):
    """How one entity's proposal reads to the owner."""

    entity: str

    def raw_details(self, change: AgentChange) -> list[str]:
        """Field lines for a change that never reached a proposal row."""

    async def details(
        self,
        session: AsyncSession,
        change: ProposalChange,
        fallback: AgentChange | None,
    ) -> list[str]:
        """Field lines for a stored change, read before it is applied."""

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        """One sentence naming what this change does."""

    async def screen(
        self, session: AsyncSession, change: ProposalChange
    ) -> ProposalScreen | None:
        """The review screen, or None to fall back to the generic change list."""


@dataclass(frozen=True, slots=True)
class ProposalRegistry:
    """Every proposal capability the running application has, keyed the way it is used."""

    handlers: Mapping[str, ProposalHandler]
    presenters: Mapping[str, ProposalPresenter]
    tools: Mapping[str, MutationToolSpec]
    # The read surface both `query_data` and a saved Request are validated against.
    views: frozenset[str]
    # How the application reports the state a proposal is made and saved against.
    world: WorldReader

    def handler(self, entity: str) -> ProposalHandler:
        found = self.handlers.get(entity)
        if found is None:
            raise DomainError(f"No feature owns {entity} changes")
        return found

    def presenter(self, entity: str) -> ProposalPresenter | None:
        return self.presenters.get(entity)

    def needs_confirmation(self, change: ProposalChange) -> bool:
        """Whether the owning feature refuses this change without a second confirmation."""
        handler = self.handlers.get(change.entity)
        declared = getattr(handler, "destructive_actions", frozenset())
        return change.action in declared

    def change_from_tool(self, name: str, arguments: dict[str, Any]) -> AgentChange:
        """Validate a model tool call and convert it into an application command intent."""
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown mutation tool: {name}")
        return tool.change_from(arguments)


# ------------------------------------------------------------------ shared wording

# The receipt renders one line under any outcome, so the verb stays imperative:
# "🗑 Discarded — New Tag “X”" cannot be misread as a Tag that now exists.
ACTION_VERBS = {
    ChangeAction.CREATE: "New",
    ChangeAction.UPDATE: "Edit",
    ChangeAction.MOVE: "Move",
    ChangeAction.COMPLETE: "Complete",
    ChangeAction.CANCEL: "Cancel",
    ChangeAction.REOPEN: "Reopen",
    ChangeAction.ARCHIVE: "Archive",
    ChangeAction.DELETE: "Delete",
    ChangeAction.LINK: "Link",
    ChangeAction.UNLINK: "Unlink",
}

# A feature that words one of its own fields differently passes its map in; nothing here
# holds a table of every field every feature has.
NO_LABELS: Mapping[str, str] = MappingProxyType({})


def result_value(value: Any) -> str:
    return " ".join(str(value).split())[:100]


def detail_value(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None or value == "" or value == []:
        return "—"
    if isinstance(value, list):
        return ", ".join(result_value(item) for item in value) or "—"
    return result_value(value)


def detail_label(field: str, labels: Mapping[str, str] = NO_LABELS) -> str:
    """A field as its screen label, unless its feature words that one differently."""
    return labels.get(field) or field.replace("_", " ").title()


def detail_lines(
    fields: Mapping[str, Any], labels: Mapping[str, str] = NO_LABELS
) -> list[str]:
    return [
        f"{detail_label(field, labels)}: {detail_value(value)}"
        for field, value in fields.items()
    ]


def display_diff_value(value: Any) -> str:
    """How one side of a review-screen diff reads. Empty is a dash, never a blank."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def field_diffs(
    current: Mapping[str, Any], proposed: Mapping[str, Any]
) -> tuple[str, ...]:
    """The review screen's `old → new` lines for an item with no shape of its own."""
    return tuple(
        f"• {field.replace('_', ' ').title()}: "
        f"{html.escape(display_diff_value(current.get(field)))} → "
        f"{html.escape(display_diff_value(new_value))}"
        for field, new_value in proposed.items()
        if current.get(field) != new_value
    )


async def named_summary(
    session: AsyncSession,
    change: ProposalChange,
    details: list[str],
    *,
    model: type[Any] | None,
) -> str:
    """The receipt line for an item the owner knows by its name."""
    entity = (
        await session.get(model, change.entity_id)
        if model is not None and change.entity_id is not None
        else None
    )
    values = dict(change.values)
    name = (
        values.get("name")
        or values.get("title")
        or getattr(entity, "name", None)
        or getattr(entity, "title", None)
    )
    label = change.entity.title()
    head = f"{label} “{result_value(name)}”" if name else f"{label} #{change.entity_id}"
    tail = [] if change.action is ChangeAction.CREATE else list(details)
    verb = ACTION_VERBS.get(change.action, change.action.title())
    return f"{verb} {head}" + (f" ({' · '.join(tail)})" if tail else "")


async def named_details(
    session: AsyncSession,
    change: ProposalChange,
    fallback_lines: list[str],
    *,
    model: type[Any],
    labels: Mapping[str, str] = NO_LABELS,
) -> list[str]:
    """Field lines for an item whose committed row is what the proposal diffs against."""
    if change.action is ChangeAction.CREATE:
        return fallback_lines or detail_lines(dict(change.values), labels)
    entity = (
        await session.get(model, change.entity_id) if change.entity_id is not None else None
    )
    if entity is None:
        return fallback_lines or detail_lines(dict(change.values), labels)
    if change.action in {ChangeAction.ARCHIVE, ChangeAction.DELETE}:
        label = getattr(entity, "name", f"#{entity.id}")
        return [f"Item: {result_value(label)}"]
    return [
        f"{detail_label(field, labels)}: "
        f"{detail_value(getattr(entity, field, None))} → {detail_value(value)}"
        for field, value in dict(change.values).items()
        if getattr(entity, field, None) != value
    ]


def reference_details(values: Mapping[str, Any], prefix: str) -> list[str]:
    result: list[str] = []
    singular = values.get(f"{prefix}_id")
    if singular is not None:
        result.append(f"#{singular}")
    result.extend(f"#{item}" for item in values.get(f"{prefix}_ids") or [])
    query = values.get(f"{prefix}_query")
    if query is not None:
        result.extend(str(item) for item in (query if isinstance(query, list) else [query]))
    return result


# ------------------------------------------------- preparing a change over references

REFERENCE_HINT = (
    "Find the item with query_data and retry this call with its numeric ID. If you "
    "proposed it earlier in this same turn, wait for that result and use the ID it returns."
)


async def validate_named_references(
    session: AsyncSession, values: dict[str, Any], spec: ReferenceSpec
) -> None:
    """Reject a relationship the owner could not act on, with a retryable hint."""
    resolved = await resolve_references(session, spec, values)
    if resolved.blank:
        raise ToolPreparationError(
            "invalid_arguments",
            f"{spec.label} name must not be empty.",
            f"Provide one exact {spec.label} name or its numeric ID.",
        )
    if resolved.unknown_ids:
        raise ToolPreparationError(
            "reference_not_found",
            f"{spec.label} #{resolved.unknown_ids[0]} does not exist"
            + (" or is archived." if spec.archivable else "."),
            REFERENCE_HINT,
        )
    if resolved.missing:
        raise ToolPreparationError(
            "reference_not_found",
            f"{spec.label} '{resolved.missing[0]}' was not found.",
            REFERENCE_HINT,
        )
    if resolved.ambiguous:
        raise ToolPreparationError(
            "reference_ambiguous",
            f"{spec.label} '{resolved.ambiguous[0]}' matched more than one item.",
            f"Use query_data to choose one {spec.label} and retry with its numeric ID.",
        )
    if spec.refusal is None:
        return
    for entity_id in sorted(resolved.ids):
        entity = await session.get(spec.model, entity_id)
        if entity is None:
            continue
        refused = await spec.refusal(session, entity)
        if refused is not None:
            raise ToolPreparationError(*refused)


async def named_ids(
    session: AsyncSession, values: dict[str, Any], spec: ReferenceSpec
) -> set[int]:
    """Resolve one relationship at approval time against committed data.

    A proposal holds one change, so a name referenced here always belongs to an
    item an earlier proposal already saved.
    """
    resolved = await resolve_references(session, spec, values)
    if resolved.unresolved:
        raise DomainError(
            f"{spec.label} '{resolved.unresolved[0]}' is not available for this approved link"
        )
    # Unknown numeric IDs stay for the domain command to reject with its own message.
    return resolved.ids | set(resolved.unknown_ids)


# --------------------------------------------------------- presenting one to the owner

async def reference_names(session: AsyncSession, spec: ReferenceSpec, value: Any) -> list[str]:
    ids = list(value or [])
    entities = (
        list(await session.scalars(select(spec.model).where(spec.model.id.in_(ids))))
        if ids
        else []
    )
    by_id = {entity.id: getattr(entity, spec.name_attr) for entity in entities}
    return [by_id[item_id] for item_id in ids if item_id in by_id]


async def reference_groups(
    session: AsyncSession,
    values: dict[str, Any],
    specs: tuple[ReferenceSpec, ...],
) -> list[str]:
    """Name the items a payload points at, for the owner."""
    groups: list[str] = []
    for spec in specs:
        if not spec.mentioned_in(values):
            continue
        resolved = await resolve_references(session, spec, values)
        names: list[str] = []
        for entity_id in sorted(resolved.ids):
            entity = await session.get(spec.model, entity_id)
            if entity is not None:
                names.append(result_value(getattr(entity, spec.name_attr)))
        names.extend(result_value(name) for name in resolved.unresolved)
        if len(names) == 1:
            groups.append(f"{spec.label} “{names[0]}”")
        elif names:
            groups.append(f"{spec.label}s {', '.join(f'“{name}”' for name in names)}")
    return groups


class NamedItemPresenter:
    """Tag and Value read the same way: a name, a description, and a field diff."""

    entity = ""
    model: type[Any]
    label = ""

    def raw_details(self, change: AgentChange) -> list[str]:
        return detail_lines(dict(change.values))

    async def details(
        self, session: AsyncSession, change: ProposalChange, fallback: AgentChange | None
    ) -> list[str]:
        fallback_lines = self.raw_details(fallback) if fallback is not None else []
        return await named_details(session, change, fallback_lines, model=self.model)

    async def summary(
        self, session: AsyncSession, change: ProposalChange, details: list[str]
    ) -> str:
        return await named_summary(session, change, details, model=self.model)

    def _current(self, item: Any) -> dict[str, Any]:
        raise NotImplementedError

    async def screen(
        self, session: AsyncSession, change: ProposalChange
    ) -> ProposalScreen | None:
        current: dict[str, Any] = {}
        archived = False
        if change.entity_id:
            item = await session.get(self.model, change.entity_id)
            if item is not None:
                archived = getattr(item, "archived_at", None) is not None
                current = self._current(item)
        proposed = {**current, **dict(change.values)}
        if change.action in {ChangeAction.ARCHIVE, ChangeAction.DELETE}:
            current["status"] = "Archived" if archived else "Active"
            proposed["status"] = "Archived" if change.action is ChangeAction.ARCHIVE else "Deleted"
        return ProposalScreen(
            mode="Create" if change.action is ChangeAction.CREATE else "Edit",
            item=self.label,
            blocks=(
                f"Name: {html.escape(display_diff_value(proposed.get('name')))}\n"
                f"Description: "
                f"{html.escape(display_diff_value(proposed.get('description')))}",
            ),
            diffs=field_diffs(current, proposed),
        )
