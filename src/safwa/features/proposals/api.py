"""What a feature contributes when it owns an entity the model may change.

One registration, three responsibilities, and they are three different layers:

- `ProposalHandler` checks a change against live data and applies an approved one — the
  application side, which calls the same domain functions the manual UI calls;
- `MutationToolSpec` is what the model sees — the schema, the one-line description and
  the conversion into a neutral `AgentChange`;
- `ProposalPresenter` is how the change reads to the owner — the receipt lines and the
  review screen.

Generic proposal code holds the orchestration: the workspace and its revision, the batch
and proposal rows, the optimistic lock. Loading the entity, `archived_at`, the closed
repeat and `target_not_found` belong to the feature that owns the entity.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import LlmProvider

from ...ai.contracts import AgentChange, tool_json_schema
from ...ai.sql import ReadOnlyQueryRunner
from ...domain import (
    CARD_REFERENCE_SPECS,
    DomainError,
    ReferenceSpec,
    is_closed_repeat,
    live_repeat_instance_id,
    resolve_references,
)
from ...models import Card, Check, ProposalChange, Workspace


class ToolPreparationError(DomainError):
    """A model-visible error for one mutation call, not for the whole agent turn."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "status": "error",
            "code": self.code,
            "error": str(self),
            "hint": self.hint,
            "retryable": True,
        }


@dataclass(frozen=True)
class PreparedChange:
    """What a proposal row needs: the resolved values and the version they assume."""

    values: dict[str, Any]
    expected_version: int | None


@dataclass(frozen=True, slots=True)
class PreparationContext:
    """Everything preparation may read. It writes nothing."""

    session: AsyncSession
    workspace: Workspace
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
    if change.id is not None and (
        entity is None or getattr(entity, "archived_at", None) is not None
    ):
        raise ToolPreparationError(
            "target_not_found",
            f"{change.entity.title()} #{change.id} does not exist or is archived.",
            "Find the current numeric ID with query_safwa and retry. If nothing matches, say so "
            "instead of proposing again.",
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
class MutationToolSpec:
    """One mutation tool as the model sees it."""

    name: str
    input_model: type[BaseModel]
    description: str
    to_change: Callable[[BaseModel], AgentChange]
    # Extra repair detail when the model sent arguments this tool could not validate.
    repair: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": tool_json_schema(self.input_model),
            },
        }

    def change_from(self, arguments: dict[str, Any]) -> AgentChange:
        return self.to_change(self.input_model.model_validate(arguments))


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
    # The read surface both `query_safwa` and a saved Request are validated against.
    views: frozenset[str]

    def handler(self, entity: str) -> ProposalHandler:
        found = self.handlers.get(entity)
        if found is None:
            raise DomainError(f"No feature owns {entity} changes")
        return found

    def presenter(self, entity: str) -> ProposalPresenter | None:
        return self.presenters.get(entity)

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
    "create": "New",
    "update": "Edit",
    "move": "Move",
    "complete": "Complete",
    "cancel": "Cancel",
    "reopen": "Reopen",
    "archive": "Archive",
    "delete": "Delete",
    "link": "Link",
    "unlink": "Unlink",
}

DETAIL_LABELS = {
    "kind": "Kind",
    "title": "Title",
    "name": "Name",
    "description": "Description",
    "note": "Note",
    "stage": "Stage",
    "priority": "Priority",
    "hard_time": "Hard Time",
    "blocked": "Blocked",
    "blocked_description": "Blocked Description",
    "effort_points": "Effort",
    "repeatable": "Repeatable",
    "categories": "Categories",
    "energy_types": "Energy",
    "parent_id": "Parent ID",
    "card_id": "Card ID",
    "outcome": "Status",
    "values": "Values",
    "tags": "Tags",
    "checks": "Checks",
    "check_ids": "Checks",
    "active": "Active",
    "query_sql": "SQL",
}


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


def detail_label(field: str) -> str:
    return DETAIL_LABELS.get(field, field.replace("_", " ").title())


def detail_lines(fields: Mapping[str, Any]) -> list[str]:
    return [f"{detail_label(field)}: {detail_value(value)}" for field, value in fields.items()]


def diff_lines(before: Mapping[str, Any], proposed: Mapping[str, Any]) -> list[str]:
    return [
        f"{detail_label(field)}: {detail_value(before.get(field))} → {detail_value(value)}"
        for field, value in proposed.items()
        if before.get(field) != value
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
    tail = [] if change.action == "create" else list(details)
    verb = ACTION_VERBS.get(change.action, change.action.title())
    return f"{verb} {head}" + (f" ({' · '.join(tail)})" if tail else "")


async def named_details(
    session: AsyncSession,
    change: ProposalChange,
    fallback_lines: list[str],
    *,
    model: type[Any],
) -> list[str]:
    """Field lines for an item whose committed row is what the proposal diffs against."""
    if change.action == "create":
        return fallback_lines or detail_lines(dict(change.values))
    entity = (
        await session.get(model, change.entity_id) if change.entity_id is not None else None
    )
    if entity is None:
        return fallback_lines or detail_lines(dict(change.values))
    if change.action in {"archive", "delete"}:
        label = getattr(entity, "name", f"#{entity.id}")
        return [f"Item: {result_value(label)}"]
    return [
        f"{detail_label(field)}: "
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
    "Find the item with query_safwa and retry this call with its numeric ID. If you "
    "proposed it earlier in this same turn, wait for that result and use the ID it returns."
)


async def live_instance_hint(session: AsyncSession, entity: Card | Check) -> str:
    live_id = await live_repeat_instance_id(session, entity)
    if live_id is None:
        return "The series has ended. Tell the owner instead of proposing again."
    return f"Retry this call with #{live_id}, the open one in its series."


async def reject_closed_repeat(session: AsyncSession, entity: Card | Check, label: str) -> None:
    if not is_closed_repeat(entity):
        return
    raise ToolPreparationError(
        "closed_repeat",
        f"{label.title()} #{entity.id} is a closed repeat and cannot be changed.",
        await live_instance_hint(session, entity),
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
            f"{spec.label} #{resolved.unknown_ids[0]} does not exist or is archived.",
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
            f"Use query_safwa to choose one {spec.label} and retry with its numeric ID.",
        )
    if spec.model is not Check:
        return
    for check_id in sorted(resolved.ids):
        check = await session.get(Check, check_id)
        if check is not None and is_closed_repeat(check):
            raise ToolPreparationError(
                "closed_repeat",
                f"Check #{check_id} is a closed repeat and cannot be linked.",
                await live_instance_hint(session, check),
            )


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
    specs: tuple[ReferenceSpec, ...] = CARD_REFERENCE_SPECS,
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
                archived = item.archived_at is not None
                current = self._current(item)
        proposed = {**current, **dict(change.values)}
        if change.action in {"archive", "delete"}:
            current["status"] = "Archived" if archived else "Active"
            proposed["status"] = "Archived" if change.action == "archive" else "Deleted"
        return ProposalScreen(
            mode="Create" if change.action == "create" else "Edit",
            item=self.label,
            blocks=(
                f"Name: {html.escape(display_diff_value(proposed.get('name')))}\n"
                f"Description: "
                f"{html.escape(display_diff_value(proposed.get('description')))}",
            ),
            diffs=field_diffs(current, proposed),
        )
