"""How a proposed Check is checked and then written."""

from __future__ import annotations

from typing import Any

from tg_agent_shell.foundation.errors import DomainError, StaleStateError
from tg_agent_shell.proposals.api import (
    ApplyContext,
    ChangeAction,
    PreparationContext,
    PreparedChange,
    ProposalChange,
    ToolPreparationError,
    named_ids,
    require_target,
    validate_named_references,
)

from ...enums import ActorType
from ...foundation.marks import closed_repeat_refusal
from .model import Check, CheckOutcome
from .references import CHECK_VALUE_REFERENCE
from .use_cases import (
    archive_check,
    create_check,
    delete_check,
    resolve_check,
    update_check_fields,
)

# A Check is answered with the Card lifecycle verbs: complete is Passed, cancel is Missed.
CHECK_ANSWER_ACTIONS = {
    ChangeAction.COMPLETE: CheckOutcome.PASSED.value,
    ChangeAction.CANCEL: CheckOutcome.MISSED.value,
}

class CheckProposalHandler:
    entity = "check"
    # A Check proposal keeps the version it was prepared against.
    version_model: type[Any] | None = None

    async def prepare(self, context: PreparationContext, change: Any) -> PreparedChange:
        check, expected_version = await require_target(context, change, Check)
        if check is not None:
            refusal = await closed_repeat_refusal(context.session, check, change.entity)
            if refusal is not None:
                raise ToolPreparationError(*refusal)
        values = dict(change.values)
        await validate_named_references(context.session, values, CHECK_VALUE_REFERENCE)
        return PreparedChange(values=values, expected_version=expected_version)

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        session = context.session
        values = dict(change.values)
        if change.action is ChangeAction.CREATE:
            created = await create_check(
                session,
                title=str(values["title"]),
                repeatable=bool(values.get("repeatable", False)),
            )
            return [created.id]
        check = await session.get(Check, change.entity_id) if change.entity_id else None
        if check is None or check.version != change.expected_version:
            raise StaleStateError("A Check changed; refresh this proposal")
        if change.action is ChangeAction.UPDATE:
            scalar_fields = {
                name: value for name, value in values.items() if name in {"title", "repeatable"}
            }
            if scalar_fields:
                await update_check_fields(session, check.id, scalar_fields)
        elif change.action in CHECK_ANSWER_ACTIONS:
            await resolve_check(
                session, check.id, CHECK_ANSWER_ACTIONS[change.action], actor=ActorType.AI
            )
        elif change.action is ChangeAction.ARCHIVE:
            await archive_check(session, check.id)
        elif change.action is ChangeAction.DELETE:
            await delete_check(session, check.id)
        elif change.action in {ChangeAction.LINK, ChangeAction.UNLINK}:
            spec = CHECK_VALUE_REFERENCE
            for value_id in sorted(await named_ids(session, values, spec)):
                exists = await session.get(spec.link_model, spec.link_key(check.id, value_id))
                if (change.action is ChangeAction.LINK) != (exists is not None):
                    await spec.toggle(session, check.id, value_id, actor=ActorType.AI)
        else:
            raise DomainError(f"Unsupported Check action: {change.action}")
        return [check.id]
