"""Which checks on the model's own work Safwa runs, and what a creating screen compares.

Each check is a hook, registered in `bootstrap/modules.py` only while it is on here, so a
change counts from the next start. They are not on the Profile: they check the model, not
anything of the owner's.
"""

from __future__ import annotations

# A subagent response that carries changes and no plan is sent back (PR-PLAN-028).
PLAN_REQUIRED = True
# Before the answer to the owner's message is sent, one model call reads a request that made
# a change for what was asked and nothing did (AG-DONE-045).
REQUEST_REVIEW = True
# A Diary response that writes new words for a day it has not read is sent back
# (DI-READ-023).
DAY_READ_REQUIRED = True
# Before a proposal's screen is drawn, one model call reads it against the owner's words and
# saves it unseen when it is exactly what they asked for (PR-AUTO-024).
AUTOAPPROVAL = True
# A review screen that creates an item lists the open items of its type most like it, by a
# local model loaded at start; off, the model is neither downloaded nor loaded (PR-SIMILAR-030).
SIMILAR_ITEMS = True
