"""What Edit offers on a Check proposal: the controls the Check's own screen has (CH-EDIT-022).

In the order that screen lists them, under its labels and with its words. A Check's title
has no control there, and its answer is what the proposal does, so neither is offered.
"""

from __future__ import annotations

from tg_agent_shell.proposals.api import EditableField

from ....constants import SELECTOR_PAGE_SIZE
from ...schedules.api import SCHEDULE_INSTRUCTION
from ...schedules.telegram import REMIND_REVIEW_FIELD, schedule_review_field
from ..references import CHECK_VALUE_REFERENCE
from .handlers import NOTE_INSTRUCTION, NOTE_TITLE, SCHEDULE_TITLE
from .screens import value_options

CHECK_REVIEW_FIELDS = {
    "schedule": schedule_review_field("⏱ Schedule", SCHEDULE_TITLE, SCHEDULE_INSTRUCTION),
    "remind": REMIND_REVIEW_FIELD,
    "note": EditableField("📝 Note", NOTE_TITLE, instruction=NOTE_INSTRUCTION, markdown=True),
    "values": EditableField(
        "💎 Values",
        "Values",
        options=lambda session, _check_id: value_options(session),
        many=True,
        reference=CHECK_VALUE_REFERENCE,
        page_size=SELECTOR_PAGE_SIZE,
    ),
}
