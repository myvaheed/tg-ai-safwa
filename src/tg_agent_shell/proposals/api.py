"""What a feature contributes when it owns an entity the model may change.

One registration, three responsibilities, and they are three different layers:

- `ProposalHandler` checks a change against live data and applies an approved one — the
  application side, which calls the same domain functions the manual UI calls;
- `MutationToolSpec` is what the model sees — the schema, the one-line description and
  the conversion into a neutral `AgentChange`;
- `ProposalPresenter` is how the change reads to the owner — the receipt lines and the
  review screen.

This file is the contracts and what preparing a change may need. The wording a presenter
is written out of — the labels, the diff lines, the receipt of a named item — is
[render.py](render.py), which reads this one and is read by no contract here.

Generic proposal code holds the orchestration: the workspace and its revision, the batch
and its open reviews, the optimistic lock. Loading the entity, `archived_at`, the closed
repeat and `target_not_found` belong to the feature that owns the entity.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel
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
    # The one sentence the second confirmation shows. What is actually lost is the owning
    # feature's to say — the screen knows only that this is the last press.
    destructive_warning: str

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

    def confirmation(self, change: ProposalChange) -> str | None:
        """What the owning feature warns before this change, or None to save it outright."""
        handler = self.handlers.get(change.entity)
        if change.action not in getattr(handler, "destructive_actions", frozenset()):
            return None
        return getattr(handler, "destructive_warning", "") or (
            f"This permanently removes the {change.entity} it names."
        )

    def change_from_tool(self, name: str, arguments: dict[str, Any]) -> AgentChange:
        """Validate a model tool call and convert it into an application command intent."""
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown mutation tool: {name}")
        return tool.change_from(arguments)


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
