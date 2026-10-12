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
