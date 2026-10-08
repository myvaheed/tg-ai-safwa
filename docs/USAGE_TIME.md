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

An owner event's processing is another interval: transcription, image reading, tools,
subagents, checks required by the request and answer delivery all belong to it. Completing
the handler adds two minutes for reading. A review screen ends that processing interval;
the owner's decision starts new work, so a long pause before Save or Discard is excluded.
Cancellation and an unhandled failure close the wait without a reading window. An error
message delivered by the handler is a response the owner can read.

Intervals are merged before summing. A one-minute recording sent at 12:00, answered at
12:04 with no further owner activity, eventually counts 11:59–12:06: seven minutes.
During that interaction it counts only up to the current moment.

Autonomous hooks and Cue turns have no owner-event scope and create no usage. Work after
the answer is explicitly outside that scope, even when the original handler awaits it.
If the owner responds to a Cue or presses its Save button, that action and the work it
requests count as owner activity.

## Storage and lifetime

The shell keeps the intervals in the new `usage_intervals` table, separate from
`telegram_messages`. Clearing the conversation does not clear usage. The owner and event
key are unique, so repeated delivery does not add the same activity or wait again; a
voice transcript is an outgoing message, not a second owner event.

[UsageRecorder](../src/tg_agent_shell/usage/recorder.py) owns the handler's processing scope
and its checkpoint task. It records elapsed work every 30 seconds, using a monotonic
clock for duration and UTC timestamps for persistence. Normal completion records the end
and cancels that task. Cancellation while opening the interval also closes it, even if
the insert was already committed. Closing matches the owner and event key, so an insert
rolled back by cancellation cannot close another interval that reuses its numeric ID.
A failed checkpoint is logged and retried on the next
30-second tick. Startup ends every interrupted interval at its last successful checkpoint,
never at restart time; with successful checkpoints, a crash can lose up to 30 seconds of
unconfirmed work. Database failures can leave a longer gap until the next successful tick.

[use_cases.py](../src/tg_agent_shell/usage/use_cases.py) owns the writes and interval union.
Its caller owns the transaction. Applications enable recording by providing a recorder
on Services; Safwa does so in its composition root. No polling loop is needed to count
the two-minute window, and no AI view or mutation tool is added.

The behavior is held by TG-USAGE-024 through TG-EVENT-030 in the
[chat scenarios](../tests/brd/tg_agent_shell/telegram_history.feature) and PS-USAGE-022 in
the [Profile scenarios](../tests/brd/profile.feature).
