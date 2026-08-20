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
# The default Sprint length; the owner overrides it per workspace in Settings.
SPRINT_LENGTH_DAYS = 14
SPRINT_LENGTH_MIN_DAYS = 2
SPRINT_LENGTH_MAX_DAYS = 60
# How a closed instance of a repeat series is named wherever its title is read: the model
# sees it in `ai_cards`/`ai_checks`, the owner sees it in a citation link. 
REPEAT_MARKER = " [🔄{index}]"
# Weekday tokens as stored in `reminders.weekdays`, indexed by `date.weekday()`.
# Mirrored by the Literal in ai/contracts.py.
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# --- AI context -----------------------------------------------------------
# How many critical Cards the planning state names before the model has to query for more.
CONTEXT_CRITICAL_CARD_LIMIT = 10

# --- AI agent loop --------------------------------------------------------
MAX_TOOL_CALLS = 64
MAX_REPAIR_ROUNDS = 5
SUSPENDED_BATCH_LOOKUP_LIMIT = 50
# A session waiting on a screen is abandoned once its proposal can no longer be acted on;
# the proposal expires in a day, so two is past every screen the owner could still answer.
SESSION_IDLE_DAYS = 2

# --- Subagents ------------------------------------------------------------
# A subagent blocks the advisor's turn, so the clock bounds it instead of a call count.
SUBAGENT_DEADLINE_SECONDS = 300.0

SUBAGENT_HISTORY_LAST_MESSAGES = 10
# How much of a day's conversation the Diary reader may hand back in one call.
DIARY_DAY_TOKEN_BUDGET = 12_000
# Local clock the Diary's system Reminder fires on out of the box; Settings moves it,
# and `off` there removes the row.
DIARY_TIME_DEFAULT = "22:00"
# Genitive month names keep compact Diary labels natural in the Russian UI, e.g. "16 августа".
DIARY_MONTH_NAMES = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
# How the day felt, 0-10. 5 is an ordinary day; 0 is the owner's own word, never the model's.
FEELING_SCORE_EMOJI = {
    0: "⚫",
    1: "😨",
    2: "😞",
    3: "🙁",
    4: "😕",
    5: "😐",
    6: "🙂",
    7: "😊",
    8: "😃",
    9: "🤩",
    10: "🌟",
}

# --- query_safwa result caps ----------------------------------------------
# Sized for a local model: one result should inform a turn, not consume its context.
DEFAULT_ROW_LIMIT = 50
DEFAULT_CHAR_BUDGET = 12_000
DEFAULT_COLUMN_LIMIT = 20
DEFAULT_CELL_LIMIT = 2_000
QUERY_TIMEOUT_SECONDS = 2.0

# --- Telegram history -----------------------------------------------------
# The advisor window is a token budget rather than a message count: a Summary of at most
# SUMMARY_TOKEN_CEILING plus the messages SUMMARY_TRIGGER_TOKENS pays for.  The trigger is
# the message budget itself, so a Summary is written exactly when the window is full.
SUMMARY_TOKEN_CEILING = 2_000
SUMMARY_TRIGGER_TOKENS = 8_000
HISTORY_TOKEN_BUDGET = SUMMARY_TRIGGER_TOKENS + SUMMARY_TOKEN_CEILING
SUMMARY_CONTEXT_MESSAGE_LIMIT = 20
# A ceiling on how many Telegram messages one backwards scan may walk.  The budget, a
# Summary, or the oldest registration normally stops it far sooner.
HISTORY_SCAN_LIMIT = 2_000
# Bot API and Telethon disagree on message IDs; correlate by timestamp within this window.
MESSAGE_CORRELATION_SECONDS = 15
# The interface owns the Saved/Discarded/Failed line.  History replays it as a tool result
# instead of as words Safwa said, so each prefix says what it meant.
RECEIPT_MEANINGS = {
    "✅ Saved": "applied",
    "⚡ Auto-saved": "applied",
    "🗑 Discarded": "not applied, the user rejected it",
    "⚠️ Failed": "not applied, it failed",
}

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
# The Sprint plan puts both columns in one table, so a row is one Card on each side.
SPRINT_PLAN_PAGE_SIZE = 10
SPRINT_PLAN_TITLE_LIMIT = 24
REQUEST_RESULT_LIMIT = 25
# How long a Toast stays on screen before it removes itself.
TOAST_SECONDS = 5
# A tap on a link starts the bot through the owner's own account, and Telegram rate limits
# that per account for hours at a time. This many taps inside the window earns a warning.
PLAN_LINK_BURST_TAPS = 8
PLAN_LINK_BURST_SECONDS = 10
CHECK_LIST_LIMIT = 25
TELEGRAM_TEXT_LIMIT = 3_900
# A queue notice is transient and deleted on drain, so it previews the turn rather than
# repeating it — a transcribed monologue would not fit in one message anyway.
QUEUE_PREVIEW_CHARS = 300
CALLBACK_TOKEN_TTL_HOURS = 24
# A resolved proposal stays in the dialogue for good, so its receipt is capped rather than
# carrying every field of a wide edit.
PROPOSAL_OUTCOME_DETAIL_LIMIT = 6

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

# --- Speech recognition ---------------------------------------------------
# One call covers the upload and the whole file's decode, so the budget follows the audio.
ASR_TIMEOUT_BASE_SECONDS = 60.0
ASR_TIMEOUT_PER_AUDIO_SECOND = 1.0
# Longer audio is refused with a plain message rather than left to time out.
ASR_MAX_DURATION_SECONDS = 1_800
# The Bot API refuses to serve a file larger than this, whatever the provider accepts.
ASR_MAX_FILE_BYTES = 20 * 1024 * 1024
# An upload is expensive to repeat, so a failure is retried less eagerly than a chat call.
ASR_MAX_RETRIES = 2
OPENAI_ASR_BASE_URL = "https://api.openai.com/v1"
GROQ_ASR_BASE_URL = "https://api.groq.com/openai/v1"
# The default for a whisper server the owner runs themselves.
LOCAL_ASR_BASE_URL = "http://127.0.0.1:8000/v1"
OPENAI_ASR_MODEL = "gpt-4o-mini-transcribe"
GROQ_ASR_MODEL = "whisper-large-v3-turbo"
LOCAL_ASR_MODEL = "Systran/faster-whisper-small"
# The in-process engine. `small` fits ~1 GB; `large-v3-turbo` is the upgrade.
FASTER_WHISPER_MODEL = "small"
# CTranslate2 has no float16 kernel on the CPU, so each device carries its own default.
FASTER_WHISPER_CPU_COMPUTE_TYPE = "int8"
FASTER_WHISPER_CUDA_COMPUTE_TYPE = "float16"
# A CPU fallback drops these rather than let CTranslate2 silently widen them to float32.
FASTER_WHISPER_CUDA_ONLY_COMPUTE_TYPES = frozenset({"float16", "int8_float16"})
# The `nvidia-*-cu12` wheels of the asr-cuda extra, whose DLLs CTranslate2 loads by name.
CUDA_RUNTIME_PACKAGES = ("cublas", "cudnn", "cuda_nvrtc")
FASTER_WHISPER_BEAM_SIZE = 5
# A local decode is silent for minutes, so it reports a percentage. Shorter audio
# finishes before the first edit would land, and Telegram rate-limits edits.
ASR_PROGRESS_MIN_AUDIO_SECONDS = 60.0
ASR_PROGRESS_MIN_INTERVAL_SECONDS = 5.0
