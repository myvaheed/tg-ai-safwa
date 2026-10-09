# Home dashboard

`/start` draws Home with one `☰ Menu` button. Pressing it or `↩️ Menu` opens the dashboard with
the whole menu (HM-START-011). Automatic cleanup and `/clear` leave the chat empty, without
requesting the Values' words or drawing Home. Unanswered hooks after the last user dialogue
are held for restoration before the next `/start` dashboard. The conversation Safwa reads
starts after the persistent clear boundary. The rules are HM-QUIET-003 to HM-CLEAR-012
in [home.feature](../tests/brd/home.feature) and PS-HOME-018 in
[profile.feature](../tests/brd/profile.feature); what a Home message does to the chat is the
shell's, TG-HOME-023 in [telegram_history.feature](../tests/brd/tg_agent_shell/telegram_history.feature),
and the check that draws it is a hook on a schedule, AG-HOOK-049 and AG-HOOK-050 in
[agents.feature](../tests/brd/tg_agent_shell/agents.feature).

With Secret word set, automatic clearing instead locks access and hides its unanswered hooks.
Reminder and Schedule Remind answers still reach the chat without links; new hook requests wait.
Entering the word deletes attempts, restores messages with links and runs waiting hooks.
Home appears only on `/start`; the conversation boundary precedes the restored hooks.
The access gate and durable delivery are described in [CHAT_ACCESS.md](CHAT_ACCESS.md).

## How it looks

```text
🏠 Sat, 27 Sep · 14:32

🗒 Latest changes
14:05 ✅ ⭐️ Write the outline · ⚡2 — done
13:40 ✏️ ⭐️ Pay rent · ⚡0.5 — priority
26.09 21:10 🗑 Old plan — deleted
…

🎯 Priority Goals
🎯 Launch the blog · ⚡3/8
🎯 Run a marathon · ⚡1/13

💎 Values in focus
💎 Family
💎 Health — Three runs last week and a calm evening in the Diary: a short walk today keeps it going.

☀️ Today · 4
Planned: 4 Actions
🎯 Launch the blog · ⚡3/8
    ⭐️ Write the first post · 🧠·⚡2
    ⭐️ Pick a domain · ⚡1
⭐️ Call the bank · ⚡1
⭐️ Pay rent · ⚡0.5

⌛ Tracked today: 2h 15m over 3 Actions
```

The day and the time it was drawn head it (HM-ORDER-014), then:

- **The last `HOME_LOG_SHOWN = 5` changes**, newest first: when, an icon, the item, what
  changed. An item deleted at or after that change is its title without a link, because SQLite
  hands its id to the next item.
- **The first `HOME_GOALS_SHOWN = 5` Priority Goals**, in the order the workspace context hands
  them to Safwa (`priority_goals`, WS-CONTEXT-008).
- **Values in focus**, by name, each with the words written for it, or its name alone when
  none could be written.
- **Today**, every Action in it, in its own order (`today_actions`). An Action under a Goal
  stands under its root Goal (`goal_of` walks through every nested Goal); Goals come in the order of their
  first Action, Actions with no Goal after them. The heading counts its Card rows; the line under
  it gives the planned executions of the local day and, with EP on, their load; repeating rows
  show × quantity. With Today empty it says so, and no other list stands in for it: the Sprint and
  the Backlog are the Dashboard's (`📊 Dashboard` in the menu).
- **Time tracked today**, only while Time tracking is on: `tracked_mins` of the Actions finished
  today, workspace local day (`tracked_between`).

Every item goes through `render_citations`, so it reads and links exactly as a citation in an
answer does.

## The menu under it

[telegram.py](../src/safwa/features/home/telegram.py) draws it. `render_home` is the screen of
`nav:home`, so `/start`, `↩️ Menu` and `go` with no Place all reach it. `/start` uses
`home_markup`, one `☰ Menu` button; navigation uses `menu_markup`. Both are
`MessageKind.DASHBOARD` screens that the next
navigation replaces. It opens at once, with the words under the Values only when fresh ones are
kept; otherwise the words are asked for while it is shown and put in when they come (see
[The words under a Value](#the-words-under-a-value)).

Home follows the one-screen rule: navigation redraws it in place, while a new command or
message to the Advisor dismisses it. Its clear boundary is independent of the visible UI,
so replacing Home does not restore the earlier conversation (HM-STAYS-005, SC-LIVE-001).

## When the chat is cleared

`/clear` (`command_clear`) calls `AccessManager.clear` under the background lease. It removes
messages without showing Home or locking the open session. The owner's next action cancels
the clear while it is running.

`home.dashboard` ([hooks.py](../src/safwa/features/home/hooks.py)) is a `Run` on
`OnTick(every=HOME_LOOK_EVERY)`, 30 seconds: the hook tick poll hands it the owner's chat as
that look saw it (`ChatState`), and it clears when all three hold:

1. **The owner is quiet** for `home_after_minutes` (`HOME_AFTER_MINUTES_DEFAULT = 30`, 5 to 1440).
   `OwnerAndWritingMiddleware` stamps `Services.owner_acted_at` on every message and press of the
   owner's, in memory; starting counts as one. Safwa's own messages do not count.
2. **Something is owed**: a message is visible after the last boundary, or Secret word is set
   and the empty open session needs to lock. An empty unprotected chat stays as it is.
3. **The chat is free** (`chat_is_free`, the gate Cues use too): no turn, no review waiting, no
   session claimed. A review waiting holds the clear until it is answered or expires.

It publishes an empty request of kind `home`; no dashboard or motivation is generated.
`speak_on_schedule` takes the background lease and calls `AccessManager.lock` with protection
enabled, otherwise `AccessManager.clear`. `ChatHost.clear` deletes every id through the stored
boundary in `TELEGRAM_DELETE_BATCH = 100` a call. Both sides
share one id sequence in a private chat, so the range takes the owner's messages and commands,
the Advisor's replies, receipts, every screen and the previous Home dashboard alike, and no
editor session is left. It captures `UNASKED_KINDS` — Cues, which is how a Reminder or a hook's
question reaches the chat — sent after the last `MessageKind.DIALOGUE_USER`, then removes
their visible messages too. They are restored on protected entry or before the next `/start`.
The range starts no earlier than the oldest
message kept in the last `TELEGRAM_DELETE_WINDOW` of 48 hours: Telegram lets a bot delete nothing
older.

## What stays

The notes of `CONVERSATION_KINDS` — what was said — and held `UNASKED_KINDS` stay;
other operational notes in the range go with their messages. The Diary's `read_conversation`
reads a whole day from them (DI-READ-016), past the clear.

Legacy `MessageKind.HOME` and `MessageKind.CHAT_RESET` notes are in the history vocabulary's `resets`: the window stops at the newest
clear boundary, so the Advisor, a routed subagent and the Summary writer read only what came after it,
an older Summary included. A period read — `day_transcript`, which `read_conversation` is — goes past it. The dashboard is
a screen, and its words are never conversation. Removing Home retains its boundary without
its words, so it is no longer a screen and the model never reads it. A later clear replaces
that retained boundary with the persistent message-id boundary.

Safwa uses the shell's persistent message-id boundary for every clear, without sending a
Home reset. A day read goes past this boundary as it does past an older Home reset.

## The words under a Value

[motivation.py](../src/safwa/features/home/motivation.py): one `run_mini_session` for every Value
in focus, ending in `motivate(words)`, a list of a Value's number and its text of at most
`MOTIVATION_MAX_CHARS = 200`. The context is About me, the open Goals in `priority_goals`
order with their priorities and deadlines, the current Sprint's Success criteria (or an
explicit note that no Sprint is running), the local date and `today_actions` in Today's order, the
`MOTIVATION_DONE_ACTIONS = 10` Actions finished last and the `MOTIVATION_DIARY_ENTRIES = 2` Diary
days written last with words, however old, and previous motivation — then the Values, numbered.
The prompt prefers higher-ranked Goals relevant to a Value and connects a relevant Today Action
to the Sprint's criteria. It asks for a different angle and wording, without repeating the
previous message or merely rephrasing it. A number that names no
Value is dropped, and a Value with no words shows its name alone; a session that fails is logged
and every Value does.

The session asks with `reasoning_effort="none"`. A few sentences need no reasoning, and a local
reasoning model left to it can spend the whole `max_tokens` there and return an empty answer
cut off with `finish_reason="length"`. The provider does not ask again after such a cut: the
same request runs out of the same limit (`OpenAICompatibleProvider.complete`).

`Motivator` keeps the last words in memory for `MOTIVATION_FRESH_MINUTES = 10`, and only words:
a session that wrote none leaves nothing, so the next dashboard asks again. `fresh()` hands the
kept words to a screen that must open at once; `write()` returns them, or joins the session
already running, or starts one. The session is a task of its own, so a caller that stops waiting
— a `/start` replaced by another, an owner acting — does not stop it. The kept words are not
dropped when the Values change: a Value put in focus within those minutes shows its name alone.
The last `MOTIVATION_HISTORY_SIZE = 5` successful generations are kept separately in memory for
the next session, newest first, mapped to the current Value numbers by id. Cache hits and failed
sessions do not add history. This history survives cache expiry and resets when the bot restarts.

`/start`, `↩️ Menu` and `/clear` draw at once with `fresh()`. Without fresh words,
`_put_words_in` waits for `write()` in a task the chat spawns, then redraws the message with the
same buttons, under the background lease. It does not when the owner acted after drawing — the
check comes before the lease, so a Home left behind never holds it from a newer one, and the
owner acting cancels the lease after it — or when the message's note no longer holds the text
drawn. The quiet clear waits for `write()` and draws once, as before.
