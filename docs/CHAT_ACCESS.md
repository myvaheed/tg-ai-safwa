# Secret word

Profile → Secret word accepts exact text, including one character, whitespace and Unicode,
sent twice: it is saved only when the second entry matches the first, and a different one
starts over. A text starting with `/` is a command and never becomes the word. `off` is a
valid word too; Remove secret word disables protection.
The screen shows `set` or `off`; the word is stored only as a salted scrypt verifier in
`user_profile.secret_word_hash`, where NULL means off, and is never exposed to the Profile
subagent. Setting or changing it leaves the current session open.

When Home after elapses in a free chat, protection locks access, then clears the chat as
every automatic clear does: the Cues said after the owner last wrote to the Advisor stay.
A deletion Telegram refuses leaves access locked and is retried every
`CLEAR_RETRY_SECONDS = 60` until it succeeds or the word is entered; a refusal at startup
does not stop the bot. No clear leaves a Home. Telegram's 48-hour deletion window still applies.

Every owner update reaches the shell's access gate before command parsing, editor handling,
media download or background cancellation. Locked commands, callbacks, links, edits and
media cannot invoke their handlers, so an item link in a message shown while locked opens
nothing. A new text message matching the word exactly unlocks; other inputs receive
“Сначала введите Secret word.” Attempts retain only operational message identifiers, never
their text, and do not cancel background work. Only the last one that is words for the
Advisor enters conversation history, after unlocking.

Reminders, including Remind on a Schedule, keep firing and their answers reach the chat
while locked. The Cue queue distinguishes their text rows from hook rows. Filtering
before preparation leaves hook rows waiting without generating their requests or answers.
Turn hooks also wait for authenticated access. A Reminder's manual proposal made while
locked is Discarded through the existing approval continuation; no review or item screen
is opened.

Unlocking stops new Cue turns and scheduled publications, waits for the current turn, and
clears the chat as the lock did, so attempts and prompts go while unanswered Cues and
Reminders stay. The last attempt that is words for the Advisor — text but a command, a
recording or a photo, perhaps sent without knowing the chat was locked — stays too, unless
it is a try at the word: a text of at most `SECRET_LIKE_LENGTH = 5` characters, or at most
`SECRET_LIKE_DISTANCE = 3` Levenshtein edits from the word. Only a verifier is stored, so
the distance is measured at unlock, against the word just entered; an earlier attempt that
is words then stays in place of a later try. The middleware hands the gate a way to pass each
update through it again; the gate keeps that, with the message, for every such attempt until
unlock, in memory only, so a restart clears them with the other attempts. Once access opens,
the attempt that stays passes the gate again as a new owner message: it enters the
conversation and is answered. A mistyped word farther from the word than that is taken for
words and answered too. With such an attempt the waiting Cue requests are left to the next
poll that finds the chat free; without one, unlocking runs them. Access then opens without a Home
dashboard and the Home after timer restarts. An error leaves access closed and the waiting
Cue requests queued for the next entry. Commands and item links arriving after the secret
has matched wait until access opens, then reach their handlers. Other inputs received before
verification remain attempts. Pending hooks may open screens and reviews once the secret has
matched; only Reminder proposals generated while the chat is still locked are discarded.

Every clear writes the shell history's one clear boundary, `chat_clear_boundaries`: after
what it took out, or just before the first Cue it left, as TG-CLEAR-032 says. A lock is a clear, so the
Advisor reads the unanswered Cues and Reminders it leaves and the owner's answer to them.

Startup initializes protection before starting background tasks or accepting updates.
When a word is set, it clears and locks the chat again. An unlocked `/clear` empties the
chat without locking access; a locked `/clear` is just an attempted input. An application
supplies the verifier reader to `AccessManager` and sets it on `Services.access`; access
keeps no table of its own. An optional unlock renderer lets other applications draw their
own screen after entry. Safwa opens Home only on `/start`, with one Menu button; Menu
unfolds the dashboard's navigation. Other bots can leave `Services.access` unset and retain
ordinary behavior.

The rules are PS-SECRET-023, HM-LOCK-015 and TG-LOCK-031 in
[profile.feature](../tests/brd/profile.feature), [home.feature](../tests/brd/home.feature) and
[telegram_history.feature](../tests/brd/tg_agent_shell/telegram_history.feature).
