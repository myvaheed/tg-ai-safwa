"""What more than one feature reads.

A limit one module owns lives at the top of that module.  What is left here is read on
both sides of a boundary, so it belongs to neither: this module imports nothing from
``safwa``, and ``config.py`` takes a default from wherever the limit lives.
"""

from __future__ import annotations

# The built-in Tag used by capture, its saved Request and the agents' instructions.
INBOX_TAG_NAME = "Inbox"
# How long a Value's or a Tag's description may be. Their tools tell the model the same.
DESCRIPTION_MAX_CHARS = 400

# Weekday tokens as stored in `reminders.weekdays`, indexed by `date.weekday()`.
# Mirrored by the Literal in `reminders/agent.py`.
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# How the Card and the Check tools write a Note, which holds more than one thing at once.
NOTE_REPLACED_WHOLE = (
    "On update, it replaces the whole Note: write what it holds now, then the new words."
)
# How long before its moment Remind may fire.
REMIND_LEAD_MAX_MINUTES = 24 * 60
# Remind's minutes, as `remind` takes them and a new item's own tool does.
REMIND_DESCRIPTION = (
    "Remind the user at its time, 0, or this many minutes before it: "
    "'remind me' is 0, 'remind me 25 minutes before' is 25, '3 hours before' is 180."
)
NEW_ITEM_REMIND_DESCRIPTION = REMIND_DESCRIPTION + " Only on create."
# How a subagent that proposes changes goes through a turn, and how it reads the workspace.
# The workspace mutator says both; the reminder subagent, handed what it needs and reading
# nothing, says the turn without its first step. One wording: two copies drift.
_PROPOSED = (
    "With the mutation tools, in the same response: one line of text naming what you will change.",
    "After the review: one short sentence naming what was saved. The interface prints the Saved/Discarded/Failed receipt itself.",
)
_PROPOSING_RULES = """The user may override your proposal; what they saved is their choice. It takes priority. Never undo or redo it on your own.
Nothing to change, or the request is not yours: call nothing_to_do with the reason."""


def _turn(*steps: str) -> str:
    numbered = "\n".join(f"{number}. {step}" for number, step in enumerate(steps, start=1))
    return f"# How a turn goes\n{numbered}\n{_PROPOSING_RULES}"


PROPOSING_TURN = _turn(
    "Read what you need with `query_data`. Never in the same response as a mutation tool.",
    *_PROPOSED,
)
PROPOSING_FROM_CONTEXT = _turn(*_PROPOSED)
READING_THE_WORKSPACE = """# Read the data
`query_data` runs one read-only `SELECT` or `WITH ... SELECT` over these views only.
Every value listed under a view is the lowercase code stored in that column.
Find an item the user describes in other words with `search`. Nothing close: call nothing_to_do and say so.

{views}

`created_at` and `updated_at` are UTC text: compare them with `datetime('now')`.
IDs are small integers. Never ask the user for one you can find yourself."""
# How many rows one page of a Card or a Check selector lists.
SELECTOR_PAGE_SIZE = 10
# The advisor window is a token budget rather than a message count: a Summary of at most
# SUMMARY_TOKEN_CEILING plus the messages SUMMARY_TRIGGER_TOKENS pays for.  The trigger is
# the message budget itself, so a Summary is written exactly when the window is full.
# Summary writes it; the shell's history source sizes the window.
SUMMARY_TOKEN_CEILING = 2_000
SUMMARY_TRIGGER_TOKENS = 6_000
# How often the Cue queue and the hook tick poll look, and how often Reminders are checked.
SCHEDULER_POLL_SECONDS = 30.0
