"""Every tuning constant in one place.

A limit, budget, cap, or interval belongs here rather than beside the code that
happens to use it, so a knob can be found and changed without grepping.  This
module imports nothing from ``safwa``, so any module may import it and
``config.py`` can take its defaults from here without an import cycle.

Values the owner may want to override per environment are exposed as
``SAFWA_*`` settings in :mod:`safwa.config`; the constants below are their
defaults.  Everything else is fixed at the code level.
"""

from __future__ import annotations

# --- Domain ---------------------------------------------------------------
# The allowed Action effort scale.  Mirrored by the Literal in ai/contracts.py.
EFFORT_POINTS = {1, 2, 3, 5, 8, 13}
SPRINT_LENGTH_DAYS = 14
# Weekday tokens as stored in `reminders.weekdays`, indexed by `date.weekday()`.
# Mirrored by the Literal in ai/contracts.py.
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# --- AI agent loop --------------------------------------------------------
MAX_TOOL_CALLS = 64
MAX_REPAIR_ROUNDS = 5
SUSPENDED_BATCH_LOOKUP_LIMIT = 50

# --- query_safwa result caps ----------------------------------------------
# Sized for a local model: one result should inform a turn, not consume its context.
DEFAULT_ROW_LIMIT = 50
DEFAULT_CHAR_BUDGET = 12_000
DEFAULT_COLUMN_LIMIT = 20
DEFAULT_CELL_LIMIT = 2_000
QUERY_TIMEOUT_SECONDS = 2.0

# --- Telegram history -----------------------------------------------------
HISTORY_RECENT_LIMIT = 120
HISTORY_CONTINUITY_LIMIT = 500
SUMMARY_CONTEXT_MESSAGE_LIMIT = 20
# Bot API and Telethon disagree on message IDs; correlate by timestamp within this window.
MESSAGE_CORRELATION_SECONDS = 15

# --- Summaries and memory -------------------------------------------------
SUMMARY_TRIGGER_TOKENS = 10_000
MEMORY_TOKEN_BUDGET = 4_000
TOKEN_CHARS_ESTIMATE = 3.0
MEMORY_RETELL_CHUNK_TOKENS = 2_000
MEMORY_RETELL_OVERLAP_TOKENS = 500

# --- Background loops -----------------------------------------------------
MEMORY_POLL_SECONDS = 5.0
SCHEDULER_POLL_SECONDS = 30.0
MEMORY_MAINTENANCE_INTERVAL_SECONDS = 60.0

# --- Reminders ------------------------------------------------------------
# How many due Reminders one escalation may carry.  Everything the poll found goes over
# in a single advisor turn; the rest stay overdue and the next tick takes them.
REMINDER_FIRE_BATCH = 3
# How late a missed *repeat* may still fire.  Past this it rolls forward silently, so a
# weekend offline cannot produce 32 escalations at once.  A one-shot ignores this and
# always fires, however late.
REMINDER_CATCHUP_GRACE_MINUTES = 120
REMINDER_MIN_INTERVAL_MINUTES = 5
# A mini-session has one job and one terminal tool; it does not get the agent loop's budget.
MINI_SESSION_REPAIR_ROUNDS = 3
MINI_SESSION_MAX_TOOL_CALLS = 6

# --- Telegram UI ----------------------------------------------------------
PAGE_SIZE = 5
SELECTOR_PAGE_SIZE = 10
REQUEST_RESULT_LIMIT = 25
CHECK_LIST_LIMIT = 25
TELEGRAM_TEXT_LIMIT = 3_900
CALLBACK_TOKEN_TTL_HOURS = 24

# --- Provider -------------------------------------------------------------
AI_TIMEOUT_SECONDS = 120.0
AI_MAX_OUTPUT_TOKENS = 4096
LMSTUDIO_BASE_URL = "http://localhost:1234/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# A local server either answers or is down; a metered remote returns 429/502 and
# is worth retrying with the SDK's backoff.
AI_MAX_RETRIES_LOCAL = 1
# A provider can answer HTTP 200 with nothing in it, which the client's own retries do
# not cover because the request itself succeeded.
AI_EMPTY_RESPONSE_ATTEMPTS = 2
AI_MAX_RETRIES_REMOTE = 3
# Sent to OpenRouter as HTTP-Referer/X-Title for request attribution.
AI_APP_URL = "https://github.com/myvaheed/tg-ai-safwa"
AI_APP_TITLE = "Safwa"
