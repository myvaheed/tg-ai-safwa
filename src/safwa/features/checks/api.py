"""What Cards may ask about the Checks on a Card.

Closing an Action asks which Check series it has no answer for, hands the answers back and
lets go of what is still Pending; a repeat successor asks for one copy per series; reopening
asks the questions again. Cards never reaches into the Check model to do any of it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...enums import ActorType
from .use_cases import apply_check_outcome, check_resolutions, drop_pending_checks, pending_checks
from .use_cases import archive_settled_checks as archive_settled_checks
from .use_cases import check_card_id as check_card_id
from .use_cases import clone_checks_for_successor as clone_checks_for_successor
from .use_cases import delete_checks_of_cards as delete_checks_of_cards
from .use_cases import reopen_checks as reopen_checks
from .use_cases import unobserved_series as unobserved_series


async def require_check_answers(
    session: AsyncSession,
    card_id: int,
    outcomes: dict[int, Any] | None,
    *,
    gated: bool,
) -> dict[int, Any]:
    """The answers this completion needs, refused before anything is written.

    `gated` is Done: every series with no answer on this Card has to be answered now.
    Cancelling abandons the work, so it asks for nothing.
    """
    pending = await pending_checks(session, card_id)
    unobserved = await unobserved_series(session, card_id) if gated else []
    return check_resolutions(pending, unobserved, outcomes)


async def settle_checks(
    session: AsyncSession,
    card_id: int,
    resolutions: dict[int, Any],
    *,
    actor: ActorType,
) -> None:
    """Write the answers a closing Card gave, then let go of what is still Pending."""
    by_id = {check.id: check for check in await pending_checks(session, card_id)}
    for check_id, outcome in sorted(resolutions.items()):
        # The Card closing is what carries the series on, so no successor opens here.
        await apply_check_outcome(session, by_id[check_id], outcome, actor, spawn=False)
    await drop_pending_checks(session, card_id)
