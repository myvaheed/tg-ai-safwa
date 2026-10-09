# Secret word

Profile → Secret word accepts exact text, including one character, whitespace, Unicode and
a leading slash. `off` (case insensitive, without surrounding whitespace) disables it.
The screen shows `set` or `off`; the word is stored only as a salted scrypt verifier and
is never exposed to the Profile subagent. Setting or changing it leaves the current session open.

When Home after elapses in a free chat, protection locks access before deleting messages.
Unlike ordinary clearing, this includes unanswered Cues and leaves no Home. Only the Cues
ordinary clearing would spare are saved for restoration: those after the last user dialogue.
Telegram's existing 48-hour deletion window still applies.

Every owner update reaches the shell's access gate before command parsing, editor handling,
media download or background cancellation. Locked commands, callbacks, links, edits and
media cannot invoke their handlers. A new text message matching the word exactly unlocks;
other inputs receive “Сначала введите Secret word.” Attempts retain only operational message
identifiers, never their text, and do not cancel background work or enter conversation history.

Reminders, including Remind on a Schedule, keep firing and their answers reach the chat
while locked. The existing Cue queue distinguishes their text rows from hook rows. Filtering
before preparation leaves hook rows waiting without generating their requests or answers.
Turn hooks also wait for authenticated access. A Reminder's hidden manual proposal is
Discarded through the existing approval continuation; no review or item screen is opened.

`ChatHost.send_parts(links=False)` removes anchors while keeping their labels. Literal URLs,
domains and mentions are displayed as a code block so Telegram cannot make them clickable.
The note keeps the original text, including citations, for history and restoration after entry.
The usual message event id acknowledges delivery; no second acknowledgement queue is needed.

Unlocking stops new Cue turns and scheduled publications, waits for the current turn,
clears attempts and prompts, restores hidden messages, and runs the waiting Cue requests. Home with Menu
is drawn last, as a dashboard rather than a conversation reset. Access then opens and the
Home after timer restarts. An error leaves access closed and the queue retained for retry.

`deferred_deliveries` contains only snapshots of previously shown unanswered Cues, including
Reminders received while locked. It holds no newly generated hidden answers or pictures.
The `event_id`, `created_at` and `updated_at` columns used by the old result queue are removed.

`chat_clear_boundaries` stores the message id through which the conversation was cleared;
it is independent of the Home screen. The Advisor reads restored hooks beyond that boundary.
Replaying a previously shown Cue moves its existing logical record to its new Telegram id,
keeping its original timestamp, so a Diary day read still contains it only once.
Its new display time determines Telegram's deletion window.
Every part of a restored text delivery carries its snapshot id in the note's `related_id`.
After a partial send or restart, that association restores one logical record rather than
capturing the already sent part as another hook. Temporary hooks start their lifetime when
shown again.

Startup initializes protection before starting background tasks or accepting updates.
When a word is set, it clears and locks the chat again, retaining queued deliveries.
An unlocked `/clear` retains its existing behavior and displays Home; a locked `/clear`
is just an attempted input. An application includes the shell's access `MODULE` to register
its tables and supplies the verifier reader and Home renderer to `AccessManager`. Other bots
can omit that module and `Services.access` and retain ordinary behavior.

The rules are PS-SECRET-023, HM-LOCK-015 and TG-LOCK-031 in
[profile.feature](../tests/brd/profile.feature), [home.feature](../tests/brd/home.feature) and
[telegram_history.feature](../tests/brd/tg_agent_shell/telegram_history.feature).
