# Usage time

Profile shows the owner's accumulated usage since tracking began, as `~2d 5h 25m`.
A day means 24 hours; zero units are omitted and the total is rounded down to whole
minutes. Below a minute it reads `~0m`. It is recalculated when Profile opens, independent
of Time tracking for Actions and Effort Points.

This estimates interaction and waiting time, rather than measuring whether Telegram is
visible. A message, command, edit or button press gives the owner a two-minute activity
window. Only elapsed time counts. Own voice messages and video notes extend that interval
backward by their duration, approximating recording time; forwarded recordings, uploaded
audio and edits do not add recording duration.

The wait while an owner event is handled counts whole: transcription, image reading, tools,
subagents, checks required by the request and answer delivery all belong to it. A delivered
answer ends the wait and gives two more minutes for reading. A review screen ends the
handling, so a long pause before Save or Discard is excluded; the owner's decision starts
new work. Cancellation and an unhandled failure end the wait as an answer does.

Each moment counts once. A one-minute recording sent at 12:00, answered at 12:04 with no
further owner activity, eventually counts 11:59–12:06: seven minutes. During that
interaction it counts only up to the current moment.

Autonomous hooks and Cue turns have no owner event and create no usage. Work after the
answer, such as the summary, is outside the wait even when the handler awaits it. If the
owner responds to a Cue or presses its Save button, that action and the work it requests
count as owner activity.

## Storage

The shell keeps one `owner_usage` row per owner: the counted `seconds` and `last_at`, the
latest moment counted. An action or the end of a wait adds the quiet gap since `last_at`, at
most `IDLE_WINDOW` of two minutes, and then the action's own span: the recording before it,
or the whole wait. `last_at` only moves forward, so overlapping time is never added twice.
Reading the total adds the open window after `last_at`, up to the current moment. Clearing
the conversation does not clear usage.

[UsageRecorder](../src/tg_agent_shell/usage/recorder.py) records owner actions and keeps the
start of an event's wait in a context variable, so `answered` can end the wait from inside
the handler before the work after the answer. Concurrent events write one at a time. A
process that stops while a request runs records no wait for it, so time until the restart
is never counted and nothing is recovered at startup.

[use_cases.py](../src/tg_agent_shell/usage/use_cases.py) owns the arithmetic. Its caller owns
the transaction. Applications enable recording by providing a recorder on Services; Safwa
does so in its composition root. No polling loop is needed, and no AI view or mutation tool
is added.

The behavior is held by TG-USAGE-024 through TG-EVENT-030 in the
[chat scenarios](../tests/brd/tg_agent_shell/telegram_history.feature) and PS-USAGE-022 in
the [Profile scenarios](../tests/brd/profile.feature).
