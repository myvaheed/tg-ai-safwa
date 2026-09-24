"""How a review reads back — to the owner in one sentence, to the model in fields.

Three audiences read the same resolved queue item.  The owner reads a receipt line, the
model reads IDs and every field the application resolved so it does not repeat its own work,
caller that routed here reads the same lines as a receipt.  All three are rendered here,
from the plain dicts the session layer stores, so the session layer never has to know what
a proposal is.

The wording a feature writes its own `ProposalPresenter` out of is here too, under one
heading: a field's label, a value the owner reads instead of a `None`, an `old → new` line,
and `NamedItemPresenter` for the items a name is the whole of.  It reads
[api.py](api.py) and nothing in those contracts reads it back, which is what keeps how a
proposal reads out of what a proposal is.
"""

from __future__ import annotations

import html
import json
import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.contracts import AgentChange, ToolResultStatus
from ..foundation.references import ReferenceSpec, resolve_references
from .api import ProposalDescription, ProposalRegistry, ProposalScreen
from .model import (
    AUTO_SAVED_RECEIPT,
    DECISION_RECEIPTS,
    RECEIPT_MEANINGS,
    BatchDecision,
    ChangeAction,
    ProposalChange,
)
from .store import ProposalStore

# A resolved proposal stays in the dialogue for good, so its receipt is capped rather
# than carrying every field of a wide edit.
PROPOSAL_OUTCOME_DETAIL_LIMIT = 6

logger = logging.getLogger(__name__)

# The reviewer saved it, so "you decided this" would be wrong in the reply.
AUTOAPPROVED = "auto"

# What the owner reads under a resolved call. A decision has its own line; a call that
# never reached a screen failed before one, and reads the same as one that failed on Save.
_RESULT_RECEIPTS = {
    **{decision.value: receipt for decision, receipt in DECISION_RECEIPTS.items()},
    ToolResultStatus.ERROR.value: DECISION_RECEIPTS[BatchDecision.FAILED],
}

_DECISION_NEXT_STEPS = {
    BatchDecision.APPROVED: (
        "This change is saved. Do not propose it again. Continue with the parts of the "
        "user's request that are still unfinished, then answer."
    ),
    BatchDecision.DISCARDED: (
        "The user rejected this change, so it does not exist. Do not retry it unless the "
        "user asks again. Continue with the rest of the request, then answer."
    ),
    BatchDecision.FAILED: (
        "Applying this change failed, so nothing was written for it. Read `error`, fix only "
        "this call, and retry it once; every other resolved call in this request stands."
    ),
}


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


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def change_label(tool: dict[str, Any]) -> str:
    change = dict(tool.get("change") or {})
    entity = str(change.get("entity", tool.get("name", "item"))).title()
    action = str(change.get("action", "change")).title()
    entity_id = change.get("id")
    values = dict(change.get("values") or {})
    label = f"{action} {entity}"
    if entity_id is not None:
        label += f" #{entity_id}"
    name = values.get("name") or values.get("title")
    if name:
        label += f" “{result_value(name)}”"
    if values.get("tag_query"):
        label += f" → Tag “{result_value(values['tag_query'])}”"
    elif values.get("value_query"):
        label += f" → Value “{result_value(values['value_query'])}”"
    elif values.get("stage"):
        label += f" → {result_value(values['stage']).title()}"
    return label


def _results_summary(
    tools: list[dict[str, Any]],
    *,
    include_preparation_errors: bool = True,
    for_display: bool = False,
) -> str:
    """Render one queue receipt.

    The owner and the model need different things from the same tools: the model reads
    IDs and every resolved field so it does not repeat its own work, while the owner
    reads one sentence per change.  ``for_display`` picks the short form, which comes
    from the ``display`` line built while the proposal still had a session.
    """
    lines = [] if for_display else ["Proposal results:"]
    for tool in tools:
        # Only mutation calls store a dict result; a read call in the same suspended
        # turn stores its rows as a list, which must not be read as an outcome.
        stored_result = tool.get("result")
        result = stored_result if isinstance(stored_result, dict) else {}
        if not include_preparation_errors and not tool.get("proposal_id"):
            continue
        if not tool.get("proposal_id") and (
            not tool.get("change") or result.get("status") != ToolResultStatus.ERROR
        ):
            continue
        status = str(result.get("status", BatchDecision.FAILED))
        prefix = _RESULT_RECEIPTS.get(status, f"⚠️ {status.title()}")
        if status == BatchDecision.APPROVED and result.get("approval_source") == AUTOAPPROVED:
            prefix = AUTO_SAVED_RECEIPT
        if for_display:
            line = f"{prefix} — " + (str(tool.get("display") or "") or change_label(tool))
        else:
            line = f"{prefix} — {change_label(tool)}"
            affected_ids = result.get("affected_ids") or []
            if status == BatchDecision.APPROVED and affected_ids:
                line += " [result ID" + ("s" if len(affected_ids) != 1 else "") + ": "
                line += ", ".join(f"#{item}" for item in affected_ids) + "]"
        error = result.get("error")
        if error:
            line += f": {result_value(error)}"
        lines.append(line)
        if not for_display:
            # The detail lines carry what was resolved rather than what the model sent:
            # parent_query/tag_query turned into IDs, and old → new values for an edit.
            # Trimming them for saved items costs the model information and invites repeats.
            lines.extend(f"  • {detail}" for detail in tool.get("details") or [])
    return "\n".join(lines) if lines and (for_display or len(lines) > 1) else ""


def results_summary(
    tools: list[dict[str, Any]],
    *,
    include_preparation_errors: bool = True,
    for_display: bool = False,
) -> str:
    """Render the queue receipt, or nothing when rendering itself fails.

    By the time this runs the approved changes are already committed, so a defect in
    one label must never abort the turn that reports them back to the owner and to
    the model.
    """
    try:
        return _results_summary(
            tools,
            include_preparation_errors=include_preparation_errors,
            for_display=for_display,
        )
    except Exception:
        logger.exception("Could not render the approval result summary")
        return ""


def compose_display_outcome(
    message: str, summaries: list[str], shown: list[str] | None = None
) -> str:
    """Attach each application-owned result receipt exactly once, under the blocks shown.

    The interface, rather than the model, owns Saved/Discarded/Failed receipts.  Approval
    batches can accumulate overlapping summary blocks, and a provider may still echo a
    receipt in wording of its own.  Anything that opens with a receipt prefix is therefore
    dropped from the body, not only a line that matches one of ours character for
    character.

    A shown block is a subagent's words the owner reads as written, so it comes first,
    whole, paragraphs and repeated lines intact, and nothing is taken out of it.
    """
    blocks = [block.strip() for block in shown or [] if block.strip()]
    rest = _receipts_and_body(message, summaries)
    return "\n\n".join([*blocks, rest] if rest else blocks)


def _receipts_and_body(message: str, summaries: list[str]) -> str:
    receipt_lines: list[str] = []
    for summary in summaries:
        for raw_line in summary.splitlines():
            line = raw_line.strip()
            if line and line not in receipt_lines:
                receipt_lines.append(line)

    body = message.strip()
    if not receipt_lines:
        return body
    body_lines = [
        line for line in body.splitlines() if not line.strip().startswith(tuple(RECEIPT_MEANINGS))
    ]
    body = "\n".join(body_lines).strip()
    receipt = "\n".join(receipt_lines)
    return f"{receipt}\n\n{body}" if body else receipt


def with_queued_siblings(result: Any, queued: int) -> Any:
    """Tell a failed call that the request's valid calls are still queued for review.

    One failed preparation never cancels its siblings, and the model has to know that
    before it retries.  Saying it here keeps it off a request that has no failures.
    """
    if not queued or not isinstance(result, dict) or result.get("status") != ToolResultStatus.ERROR:
        return result
    return {
        **result,
        "next": (
            f"{queued} other call(s) from this request were prepared and are queued for review; "
            "they were not cancelled. Wait for their results, then retry only this call."
        ),
    }


def resolved_tool_result(
    tool: dict[str, Any], decision: BatchDecision, result: dict[str, Any]
) -> dict[str, Any]:
    """Describe one resolved queue item in the tool message the model reads back.

    A bare ``{"status": "approved", "affected_ids": [9]}`` says nothing about *what* was
    saved, which is how a resumed turn ends up repeating or misreporting its own work.
    """
    payload: dict[str, Any] = {"status": decision.value, **_json_safe(result)}
    change = dict(tool.get("change") or {})
    if change.get("entity"):
        payload["entity"] = change["entity"]
    if change.get("action"):
        payload["action"] = change["action"]
    try:
        payload["summary"] = change_label(tool)
    except Exception:  # a label defect must never break an already-committed change
        logger.exception("Could not label a resolved approval queue item")
    details = list(tool.get("details") or [])
    if details:
        payload["fields"] = details
    payload["next"] = _DECISION_NEXT_STEPS.get(
        decision, "Continue with the rest of the user's request."
    )
    if decision is BatchDecision.APPROVED and result.get("approval_source") == AUTOAPPROVED:
        # The user pressed nothing, so "you saved it" would be wrong in the reply.
        payload["next"] = (
            "This one was saved without the user; they did not decide it. "
            + payload["next"]
        )
    return payload


class ProposalRenderer:
    """One open review in owner-facing words, read from the store that holds it."""

    def __init__(self, reviews: ProposalStore, registry: ProposalRegistry) -> None:
        self.reviews = reviews
        self.registry = registry

    def only_change(self, proposal_id: int) -> ProposalChange | None:
        """The change a review holds. Nothing writes a second one, and a receipt reads one."""
        proposal = self.reviews.proposal(proposal_id)
        return proposal.changes[0] if proposal is not None and proposal.changes else None

    def raw_details(self, change: AgentChange | None) -> list[str]:
        """Field lines for a change that never reached a proposal row."""
        if change is None:
            return []
        presenter = self.registry.presenter(change.entity)
        if presenter is None:
            return detail_lines(dict(change.values))
        return presenter.raw_details(change)

    async def display_line(
        self,
        session: AsyncSession,
        proposal_id: int,
        fallback: AgentChange | None,
        details: list[str],
    ) -> str:
        """One sentence describing a proposal the way the owner reads it.

        The model still gets `details`; this line trades their IDs for the names the
        owner recognises, so a receipt says which Tag landed on which Card.
        """
        change = self.only_change(proposal_id)
        if change is None:
            return change_label(
                {
                    "change": {
                        "entity": fallback.entity,
                        "action": fallback.action,
                        "id": fallback.id,
                        "values": fallback.values,
                    }
                    if fallback is not None
                    else None
                }
            )
        presenter = self.registry.presenter(change.entity)
        if presenter is None:
            return change_label(
                {
                    "change": {
                        "entity": change.entity,
                        "action": change.action,
                        "id": change.entity_id,
                        "values": dict(change.values),
                    }
                }
            )
        return await presenter.summary(session, change, details)

    async def result_details(
        self,
        session: AsyncSession,
        proposal_id: int,
        fallback: AgentChange | None,
    ) -> list[str]:
        change = self.only_change(proposal_id)
        if change is None:
            return self.raw_details(fallback)
        presenter = self.registry.presenter(change.entity)
        if presenter is None:
            return self.raw_details(fallback) or detail_lines(dict(change.values))
        return await presenter.details(session, change, fallback)

    async def describe(self, session: AsyncSession, proposal_id: int) -> ProposalDescription:
        """How one proposal reads to the owner: the same line and fields a receipt uses.

        Read it **before** applying — the field lines are a before/after diff against
        committed state, and after `apply` that diff is empty.
        """
        fields = await self.result_details(session, proposal_id, None)
        summary = await self.display_line(session, proposal_id, None, fields)
        return ProposalDescription(summary=summary, fields=fields)


def proposal_change_summary(change: ProposalChange) -> str:
    target = f" #{change.entity_id}" if change.entity_id is not None else ""
    values = ", ".join(f"{key}={value!r}" for key, value in change.values.items())
    suffix = f": {values}" if values else ""
    return f"{change.action.title()} {change.entity.title()}{target}{suffix}"


PROPOSAL_OUTCOME_HEADINGS = {
    BatchDecision.APPROVED: "✅ Saved",
    BatchDecision.DISCARDED: "🗑 Discarded",
    BatchDecision.FAILED: "⚠️ Failed",
    BatchDecision.EXPIRED: "⏳ Expired",
}


def _carries_a_value(field: str) -> bool:
    """Whether a `Label: value` detail line says anything. `—` is the empty rendering."""
    _, separator, value = field.partition(": ")
    return not separator or value.strip() not in {"", "—", "— → —"}


def proposal_outcome_text(
    decision: BatchDecision,
    summary: str,
    fields: list[str] | None = None,
    *,
    notice: str | None = None,
) -> str:
    """The one text a resolved proposal leaves in the conversation.

    Save, Discard, and the screen a navigation freezes all read the same way, so the model
    rereading the dialogue learns what happened from one shape rather than three.
    """
    parts = [f"<b>{PROPOSAL_OUTCOME_HEADINGS.get(decision, 'Resolved')}</b>"]
    if notice:
        parts.append(html.escape(notice))
    if summary:
        parts.append(html.escape(summary))
    details = [field for field in (fields or []) if _carries_a_value(field)]
    if details:
        capped = details[:PROPOSAL_OUTCOME_DETAIL_LIMIT]
        if len(details) > PROPOSAL_OUTCOME_DETAIL_LIMIT:
            capped.append(f"… and {len(details) - PROPOSAL_OUTCOME_DETAIL_LIMIT} more")
        parts.append("\n".join(f"• {html.escape(field)}" for field in capped))
    return "\n".join(parts)
