"""Prepare and save a photo's label independently of the items that use it."""

from __future__ import annotations

from ..ai.contracts import AgentChange
from ..foundation.errors import StaleStateError
from ..proposals.api import (
    ApplyContext,
    PreparationContext,
    PreparedChange,
    ProposalChange,
    ToolPreparationError,
)
from .library import MEDIA_TYPE, ChatMedia, label_words, rename_media


class MediaProposalHandler:
    entity = MEDIA_TYPE
    version_model = ChatMedia

    async def prepare(self, context: PreparationContext, change: AgentChange) -> PreparedChange:
        media = await context.session.get(ChatMedia, change.id)
        if media is None:
            raise ToolPreparationError(
                "media_not_found",
                f"No photo has the number {change.id}.",
                "Use N from a photo's [words](media:N) label in the conversation.",
            )
        return PreparedChange(
            values={"meta": label_words(str(change.values["meta"]))},
            expected_version=media.version,
        )

    async def apply(self, context: ApplyContext, change: ProposalChange) -> list[int]:
        media = await context.session.get(ChatMedia, change.entity_id)
        if media is None or media.version != change.expected_version:
            raise StaleStateError("The photo changed; refresh this proposal")
        await rename_media(context.session, media.id, str(change.values["meta"]))
        return [media.id]
