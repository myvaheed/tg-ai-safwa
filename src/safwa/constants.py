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

# The default Sprint length; the owner overrides it per workspace in Settings.
SPRINT_LENGTH_DAYS = 14
SPRINT_LENGTH_MIN_DAYS = 2
SPRINT_LENGTH_MAX_DAYS = 60
# How many Sprint endings a closed Card or Check waits before it leaves the screens.
ARCHIVE_AFTER_SPRINTS = 2
# What a title carries after it wherever it is read: the model sees the marks in
# `ai_cards`/`ai_checks`, the owner sees them on a screen and in a citation link.
# The tail of that marker, empty when the series has ended and there is no open one.
# Weekday tokens as stored in `reminders.weekdays`, indexed by `date.weekday()`.
# Mirrored by the Literal in ai/contracts.py.
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# --- AI context -----------------------------------------------------------
# How many critical Cards the workspace state names before the model has to query for more.
CONTEXT_CRITICAL_CARD_LIMIT = 10

# --- AI agent loop --------------------------------------------------------
MAX_TOOL_CALLS = 64

# --- Subagents ------------------------------------------------------------
# A subagent blocks the advisor's turn, so the clock bounds it instead of a call count.
SUBAGENT_DEADLINE_SECONDS = 300.0

# Local clock the Diary's system Reminder fires on out of the box; Settings moves it,
# and `off` there removes the row.
DIARY_TIME_DEFAULT = "22:00"

# --- Telegram history -----------------------------------------------------
# The advisor window is a token budget rather than a message count: a Summary of at most
# SUMMARY_TOKEN_CEILING plus the messages SUMMARY_TRIGGER_TOKENS pays for.  The trigger is
# the message budget itself, so a Summary is written exactly when the window is full.
SUMMARY_TOKEN_CEILING = 2_000
SUMMARY_TRIGGER_TOKENS = 6_000
# --- Summaries and memory -------------------------------------------------
MEMORY_TOKEN_BUDGET = 4_000
# A tokenizer splits Latin at roughly 4 characters and Cyrillic at roughly 2, so a mixed
# dialogue is counted low by the generous end and would overrun its budget.
TOKEN_CHARS_ESTIMATE = 2.5
MEMORY_RETELL_CHUNK_TOKENS = 2_000
MEMORY_RETELL_OVERLAP_TOKENS = 500
# Memory reads back to its own cursor rather than to a fixed message count; this only caps
# how much one catch-up run may swallow after a long gap.
MEMORY_READ_TOKEN_BUDGET = 20_000

# --- Background loops -----------------------------------------------------
MEMORY_POLL_SECONDS = 5.0
SCHEDULER_POLL_SECONDS = 30.0
# A Sprint expires at a local midnight, so this poll only has to be finer than a night.
SPRINT_EXPIRY_POLL_SECONDS = 300.0
MEMORY_MAINTENANCE_INTERVAL_SECONDS = 60.0

# --- Reminders ------------------------------------------------------------
# How many due Reminders one Cue may carry.  Everything the poll found goes over
# in a single advisor turn; the rest stay overdue and the next tick takes them.
REMINDER_FIRE_BATCH = 3
# How late a missed *repeat* may still fire.  Past this it rolls forward silently, so a
# weekend offline cannot produce 32 messages at once.  A one-shot ignores this and
# always fires, however late.
REMINDER_CATCHUP_GRACE_MINUTES = 120
REMINDER_MIN_INTERVAL_MINUTES = 5

# --- Telegram UI ----------------------------------------------------------
SELECTOR_PAGE_SIZE = 10
# The Sprint plan puts both columns in one table, so a row is one Card on each side.
SPRINT_PLAN_PAGE_SIZE = 10
SPRINT_PLAN_TITLE_LIMIT = 24
REQUEST_RESULT_LIMIT = 25
# A tap on a link starts the bot through the owner's own account, and Telegram rate limits
# that per account for hours at a time. This many taps inside the window earns a warning.
PLAN_LINK_BURST_TAPS = 8
PLAN_LINK_BURST_SECONDS = 10
CHECK_LIST_LIMIT = 25

# --- Provider -------------------------------------------------------------
AI_TIMEOUT_SECONDS = 120.0
AI_MAX_OUTPUT_TOKENS = 4096
LMSTUDIO_BASE_URL = "http://localhost:1234/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# A local server either answers or is down; a metered remote returns 429/502 and
# is worth retrying with the SDK's backoff.
AI_MAX_RETRIES_LOCAL = 1
AI_MAX_RETRIES_REMOTE = 3
# Sent to OpenRouter as HTTP-Referer/X-Title for request attribution.
AI_APP_URL = "https://github.com/myvaheed/tg-ai-safwa"
AI_APP_TITLE = "Safwa"

# --- Speech recognition ---------------------------------------------------
OPENAI_ASR_BASE_URL = "https://api.openai.com/v1"
GROQ_ASR_BASE_URL = "https://api.groq.com/openai/v1"
# The default for a whisper server the owner runs themselves.
LOCAL_ASR_BASE_URL = "http://127.0.0.1:8000/v1"
OPENAI_ASR_MODEL = "gpt-4o-mini-transcribe"
GROQ_ASR_MODEL = "whisper-large-v3-turbo"
LOCAL_ASR_MODEL = "Systran/faster-whisper-small"
# The in-process engine. `small` fits ~1 GB; `large-v3-turbo` is the upgrade.
FASTER_WHISPER_MODEL = "small"
