"""Which checks on the model's own work Safwa runs.

Each one is a hook, registered in `bootstrap/modules.py` only while it is on here, so a
change counts from the next start. They are not on the Profile: they check the model, not
anything of the owner's.
"""

from __future__ import annotations

# A subagent response that carries changes and no plan is sent back (PR-PLAN-028).
PLAN_REQUIRED = True
# Before the answer to the owner's message is sent, one model call reads the request for
# what was asked and nothing did (AG-DONE-045).
REQUEST_REVIEW = True
