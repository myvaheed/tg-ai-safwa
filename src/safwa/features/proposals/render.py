"""How a review reads back — to the owner in one sentence, to the model in fields.

Three audiences read the same resolved queue item.  The owner reads a receipt line, the
model reads IDs and every field Safwa resolved so it does not repeat its own work, and a
caller that routed here reads the same lines as a receipt.  All three are rendered here,
from the plain dicts the session layer stores, so the session layer never has to know what
a proposal is.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...ai.contracts import AgentChange, ToolResultStatus
from .api import ProposalDescription, ProposalRegistry, detail_lines, result_value
from .model import (
    AUTO_SAVED_RECEIPT,
    DECISION_RECEIPTS,
    RECEIPT_MEANINGS,
    BatchDecision,
    ProposalChange,
)
from .store import ProposalStore

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
            # The detail lines carry what Safwa resolved rather than what the model sent:
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


def compose_display_outcome(message: str, summaries: list[str]) -> str:
    """Attach each application-owned result receipt exactly once.

    The interface, rather than the model, owns Saved/Discarded/Failed receipts.  Approval
    batches can accumulate overlapping summary blocks, and a provider may still echo a
    receipt in wording of its own.  Anything that opens with a receipt prefix is therefore
    dropped from the body, not only a line that matches one of ours character for
    character.
    """
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
        payload["next"] = "Safwa saved this one itself; the user did not decide. " + payload["next"]
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
