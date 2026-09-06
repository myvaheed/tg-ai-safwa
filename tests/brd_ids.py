from __future__ import annotations

import re

SCENARIO_ID_TEXT = r"[A-Z]{2,3}-[A-Z-]+-\d{3}"
SCENARIO_ID = re.compile(rf"^(?P<id>{SCENARIO_ID_TEXT})\b")
