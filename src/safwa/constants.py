"""What more than one feature reads.

A limit one module owns lives at the top of that module.  What is left here is read on
both sides of a boundary, so it belongs to neither: this module imports nothing from
``safwa``, and ``config.py`` takes a default from wherever the limit lives.
"""

from __future__ import annotations

# What the Profile accepts as a Sprint length, and what Planning plans one for.
SPRINT_LENGTH_MIN_DAYS = 2
SPRINT_LENGTH_MAX_DAYS = 60
# Weekday tokens as stored in `reminders.weekdays`, indexed by `date.weekday()`.
# Mirrored by the Literal in `reminders/agent.py`.
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# How many rows one page of a Card or a Check selector lists.
SELECTOR_PAGE_SIZE = 10
# The advisor window is a token budget rather than a message count: a Summary of at most
# SUMMARY_TOKEN_CEILING plus the messages SUMMARY_TRIGGER_TOKENS pays for.  The trigger is
# the message budget itself, so a Summary is written exactly when the window is full.
# Continuity writes the Summary; the shell's history source sizes the window.
SUMMARY_TOKEN_CEILING = 2_000
SUMMARY_TRIGGER_TOKENS = 6_000
# The tick of both the Cue queue and the Reminder poll.
SCHEDULER_POLL_SECONDS = 30.0
