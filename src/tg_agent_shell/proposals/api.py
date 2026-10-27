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

from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway import LlmProvider

from ..ai.autoapproval import AutoApprovalRule
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
    # What the call's last preparation produced, when the owner set one of its values by
    # hand: what it read from a value left as it was need not be read again (PR-EDIT-034).
    earlier: Mapping[str, Any] | None = None
    # The conversation as the session that made the call read it, for a short session of
    # preparation to read the owner's own words in. Empty when the owner set the change
    # going, by an edit or a switch (AG-SESSION-063).
    conversation: str = ""


@dataclass(frozen=True, slots=True)
class ApplyContext:
    """Everything an approved change may use while it is written."""

    session: AsyncSession
    views: frozenset[str]
    allow_destructive: bool = False
    # What the write did beyond the change itself, in the words the session that proposed
    # it reads with its result (PR-NOTE-038).
    said: list[str] = field(default_factory=list)


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


class Effect(StrEnum):
    """What Save does to the item: how its screen marks it and what its button says."""

    CREATE = "Create"
    UPDATE = "Update"
    ARCHIVE = "Archive"
    DELETE = "Delete"


def effect_of(changes: Sequence[ProposalChange]) -> Effect:
    if changes[0].action is ChangeAction.CREATE:
        return Effect.CREATE
    if changes[-1].action is ChangeAction.DELETE:
        return Effect.DELETE
    if changes[-1].action is ChangeAction.ARCHIVE:
        return Effect.ARCHIVE
    return Effect.UPDATE


# A field's value in the owner's words, not yet escaped; a list is its items, None is empty.
FieldValue = str | tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class ShownField:
    """One field of the item a review screen shows, as it is and as Save leaves it."""

    label: str
    before: FieldValue = None
    after: FieldValue = None
    # Shown on an edit even when Save leaves it as it is: what tells the owner which item.
    anchor: bool = False


@dataclass(frozen=True, slots=True)
class ProposalScreen:
    """The review screen of one proposal, before any wording is joined.

    `fields` hold the item before and after Save, and the screen marks them by the effect.
    `blocks` are HTML the feature words itself, below them.
    """

    item: str
    fields: tuple[ShownField, ...] = ()
    blocks: tuple[str, ...] = ()
    # The heading's verb when the effect's own word says less: "Answer", "Start".
    mode: str | None = None


@dataclass(frozen=True)
class ProposalDescription:
    """One proposal in words: the line its receipt reads, plus its `Label: old → new` lines
    for a model judging it."""

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
        self, session: AsyncSession, changes: Sequence[ProposalChange]
    ) -> ProposalScreen | None:
        """The review screen of one proposal, or None to fall back to the generic change list.

        `changes` are every change the proposal holds, in the order Save applies them. More
        than one are all for one existing item: each field is before the first and after the
        last of them.
        """


@dataclass(frozen=True, slots=True)
class SimilarItems:
    """What a new item of one entity is compared with on its review screen (PR-SIMILAR-030).

    The items found are cited, so the entity publishes a screen.
    """

    # The create value that holds the new item's words.
    field: str
    # Every open item of the same entity, as (id, words).
    open_items: Callable[[AsyncSession], Awaitable[Sequence[tuple[int, str]]]]


# A field's choices, given the id of the item the proposal changes, None for a new one.
Options = Callable[[AsyncSession, int | None], Awaitable[Sequence[tuple[str, Any]]]]


@dataclass(frozen=True, slots=True)
class EditableField:
    """One value a call names that the owner may set by hand under Edit (PR-EDIT-033).

    With no `options` the owner types it; with them they pick one, or tick several when
    `many`, and a single choice with `parse` also takes typed words under "✏️ Other". Labels,
    titles and options are the item's own screen's, so the field reads the same in both
    places.
    """

    label: str
    title: str
    options: Options | None = None
    many: bool = False
    # The typed words as the call carries them; a ValueError or DomainError is the refusal.
    parse: Callable[[str], Any] | None = None
    instruction: str = ""
    # Where a list names items by name or id: how they resolve, and which a link may add.
    reference: ReferenceSpec | None = None
    # Options per screen; None shows them all at once.
    page_size: int | None = None
    # Typed words that may carry Markdown, as the item's own editor says.
    markdown: bool = False
    # A field that is what the call does rather than one of its values, as a Stage that may
    # finish the item: what a call says for it, None when it says nothing, and the call with
    # the owner's choice in its place.
    read: Callable[[AgentChange], Any] | None = None
    write: Callable[[AgentChange, Any], AgentChange] | None = None

    def said(self, key: str, call: AgentChange) -> Any:
        """What `call` gives this field."""
        return self.read(call) if self.read is not None else call.values.get(key)

    def sets(self, key: str, call: AgentChange) -> bool:
        """Whether `call` gives this field anything for the owner to change."""
        return self.read(call) is not None if self.read is not None else key in call.values

    def put(self, key: str, call: AgentChange, value: Any) -> AgentChange:
        """`call` with `value` in this field."""
        if self.write is not None:
            return self.write(call, value)
        return call.model_copy(update={"values": {**call.values, key: value}})


# Which of one call's values the owner may set by hand, in the order its item's screen lists
# them. A call is handed in because what a field is called may depend on the item it is for.
EditableFields = Callable[[AgentChange], Mapping[str, EditableField]]


@dataclass(frozen=True, slots=True)
class ProposalSwitch:
    """A button that turns a proposed change into another call, for the owner to save
    instead (PR-SWITCH-037). The feature says when it is offered and writes the call; the
    shell prepares that call as Safwa's own and puts it in the proposal's place.

    The call may carry values already read, as its preparation would leave them: they go
    to preparation as what it read before, and are not read again.
    """

    label: str
    offered: Callable[[ProposalChange], bool]
    # Reads and may ask a model; writes nothing.
    rewrite: Callable[[PreparationContext, ProposalChange], Awaitable[AgentChange]]


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
    # By entity; one absent here is created with no similar items listed.
    similar: Mapping[str, SimilarItems] = field(default_factory=dict)
    # The whole of what may be saved without the owner seeing it, by entity and action. A
    # feature that declares nothing has nothing here, and every other change takes a screen.
    autoapprovals: Mapping[tuple[str, str], AutoApprovalRule] = field(default_factory=dict)
    # By entity; one absent here offers nothing under Edit.
    editable: Mapping[str, EditableFields] = field(default_factory=dict)
    # By entity, in the order its feature declares them; one absent here turns into nothing.
    switches: Mapping[str, Sequence[ProposalSwitch]] = field(default_factory=dict)

    def offered_switches(
        self, changes: Sequence[ProposalChange]
    ) -> list[tuple[int, ProposalSwitch]]:
        """What a proposal may turn into instead, each by its place among its feature's. A
        proposal of several changes offers none: one button could not say which it turns."""
        if len(changes) != 1:
            return []
        declared = self.switches.get(changes[0].entity, ())
        return [
            (place, switch) for place, switch in enumerate(declared) if switch.offered(changes[0])
        ]

    def fields_for(self, call: AgentChange) -> Mapping[str, EditableField]:
        fields = self.editable.get(call.entity)
        return fields(call) if fields is not None else {}

    def edited(
        self, call: AgentChange, edits: Mapping[str, Any]
    ) -> tuple[AgentChange, dict[str, Any]]:
        """Safwa's `call` with the owner's values in place, and the values that still stand.

        They go in in the order the item's screen lists its fields, so a field that changes
        what the call does comes before a value that change may take away. A value equal to
        what the call already says is no edit, and one in a field the call no longer sets
        goes with it (PR-EDIT-034).
        """
        kept: dict[str, Any] = {}
        for key, editable in self.fields_for(call).items():
            if key in edits and editable.sets(key, call) and edits[key] != editable.said(key, call):
                call = editable.put(key, call, edits[key])
                kept[key] = edits[key]
        return call, kept

    def current(self, change: ProposalChange) -> AgentChange | None:
        """The call a change is prepared from now: Safwa's, with the owner's values."""
        if change.source is None:
            return None
        return self.edited(change.source, change.edits)[0]

    def edit_fields(
        self, changes: Sequence[ProposalChange]
    ) -> list[tuple[int, str, EditableField]]:
        """What Edit offers on a proposal: each field one of its calls sets that its feature
        lets the owner set, as (change index, key, field). A value preparation left out, such
        as one the workspace has switched off, is not saved, so it is not offered either. A
        field two calls set is left out, since one button could not say which of them it
        sets."""
        offered: list[tuple[int, str, EditableField]] = []
        for index, change in enumerate(changes):
            call = self.current(change)
            if call is None or change.source is None:
                continue
            offered.extend(
                (index, key, field)
                for key, field in self.fields_for(change.source).items()
                if field.sets(key, call) and (field.read is not None or key in change.values)
            )
        named = Counter(key for _index, key, _field in offered)
        return [item for item in offered if named[item[1]] == 1]

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

    def criterion(self, change: ProposalChange) -> str | None:
        """How the owning feature judges this change saved unseen; None when it is not listed."""
        rule = self.autoapprovals.get((change.entity, change.action.value))
        return rule.criteria if rule is not None and rule.accepts(change.values) else None

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

    A proposal changes one item, and a new item is always a proposal of its own, so a name
    referenced here always belongs to an item an earlier proposal already saved.
    """
    resolved = await resolve_references(session, spec, values)
    if resolved.unresolved:
        raise DomainError(
            f"{spec.label} '{resolved.unresolved[0]}' is not available for this approved link"
        )
    # Unknown numeric IDs stay for the domain command to reject with its own message.
    return resolved.ids | set(resolved.unknown_ids)


async def set_named_links(
    session: AsyncSession,
    spec: ReferenceSpec,
    owner_id: int,
    values: dict[str, Any],
    *,
    linked: bool,
    actor: Any,
) -> None:
    """Link or unlink every item the approved change names, leaving each already right."""
    for entity_id in sorted(await named_ids(session, values, spec)):
        exists = await session.get(spec.link_model, spec.link_key(owner_id, entity_id))
        if linked != (exists is not None):
            await spec.toggle(session, owner_id, entity_id, actor=actor)
