"""The photo-label tool an application explicitly grants to its subagents."""

from __future__ import annotations

from pydantic import Field, PositiveInt

from ..ai.contracts import AgentChange, ChangeAction, ToolInput
from ..proposals.api import MutationToolSpec
from .library import DESCRIPTION_MAX_WORDS, MEDIA_TYPE


class RenameMediaInput(ToolInput):
    media_id: PositiveInt = Field(description="N from the photo's [words](media:N) label.")
    meta: str = Field(
        description=f"At most {DESCRIPTION_MAX_WORDS} words: what the photo shows, as the user "
        "names it.",
    )


def _rename_change(call: RenameMediaInput) -> AgentChange:
    return AgentChange(
        entity=MEDIA_TYPE, action=ChangeAction.UPDATE, id=call.media_id, values={"meta": call.meta}
    )


RENAME_MEDIA_TOOL = MutationToolSpec(
    name="rename_media",
    input_model=RenameMediaInput,
    description="Propose new words for a photo's label.",
    to_change=_rename_change,
)
