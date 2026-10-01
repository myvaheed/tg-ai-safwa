# Home dashboard

When the owner does nothing in the chat for the Profile's quiet time, Safwa clears messages
through the last message the owner sent to the Advisor, inclusive. Messages after it stay,
and a silent Home dashboard is added, built from the workspace at that moment.
The conversation Safwa reads starts over after it. The rules are HM-QUIET-003 to HM-HISTORY-010
in [home.feature](../tests/brd/home.feature) and PS-HOME-018 in
[profile.feature](../tests/brd/profile.feature); what a Home message does to the chat is the
shell's, TG-HOME-023 in [telegram_history.feature](../tests/brd/tg_agent_shell/telegram_history.feature),
and the check that draws it is a hook on a schedule, AG-HOOK-049 and AG-HOOK-050 in
[agents.feature](../tests/brd/tg_agent_shell/agents.feature).

## How it looks

```text
🏠 Sat, 27 Sep

☀️ Today · 5 of 8
🎯 Launch the blog · ⚡3/8
    ⭐️ Write the first post · 🧠·⚡2
    ⭐️ Pick a domain · ⚡1
⭐️ Call the bank · ⚡1
⭐️ Pay rent · ⚡0.5

💎 Values in focus
💎 Family
💎 Health — Three runs last week and a calm evening in the Diary: a short walk today keeps it going.

⌛ Tracked today: 2h 15m over 3 Actions

🗒 Latest changes
14:05 ✅ ⭐️ Write the outline · ⚡2 — done
13:40 ✏️ ⭐️ Pay rent · ⚡0.5 — priority
26.09 21:10 🗑 Old plan — deleted
…
```

- **The next Actions**: the first `HOME_ACTIONS_SHOWN = 5` of Today in its own order
  (`today_actions`); with Today empty, of the Sprint; with that empty, of the Backlog — both in
  the order every other Card list uses (`list_order`). The heading names the list and how many it
  holds. An Action under a Goal stands under it (`goal_of` skips a Subgoal between them); Goals
  come in the order of their first Action, Actions with no Goal after them.
- **Values in focus**, by name, each with the words written for it, or its name alone when
  none could be written.
- **Time tracked today**, only while Time tracking is on: `tracked_mins` of the Actions finished
  today, workspace local day (`tracked_between`).
- **The last `HOME_LOG_SHOWN = 10` changes**, newest first: when, an icon, the item, what
  changed. An item deleted at or after that change is its title without a link, because SQLite
  hands its id to the next item.

Every item goes through `render_citations`, so it reads and links exactly as a citation in an
answer does. The message has no buttons; `/start` is still the menu.

## When the chat is cleared

`home.dashboard` ([hooks.py](../src/safwa/features/home/hooks.py)) is a `Run` on
`OnTick(every=HOME_LOOK_EVERY)`, 30 seconds: the hook tick poll hands it the owner's chat as
that look saw it (`ChatState`), and it clears when all three hold:

1. **The owner is quiet** for `home_after_minutes` (`HOME_AFTER_MINUTES_DEFAULT = 30`, 5 to 1440).
   `OwnerAndWritingMiddleware` stamps `Services.owner_acted_at` on every message and press of the
   owner's, in memory; starting counts as one. Safwa's own messages do not count. A Reminder
   said while the owner is away stays when the dashboard is drawn.
2. **Something is owed**: the newest message kept is not a dashboard, or is one drawn on an
   earlier local day, or the owner acted after it.
3. **The chat is free** (`chat_is_free`, the gate Cues use too): no turn, no review waiting, no
   session claimed. A review waiting holds the clear until it is answered or expires.

It writes the Values' words first and publishes the dashboard as Markdown of kind `home`.
`speak_on_schedule` drops it if the owner acted since that look, renders it the way an answer
is rendered, and takes the background lease for the sending alone, so the owner acting stops it
there too. `clear_draw_home` sends it as a new, silent message of `MessageKind.HOME`, and
`ChatHost.clear` deletes every id through the last `MessageKind.DIALOGUE_USER`, inclusive, in
`TELEGRAM_DELETE_BATCH = 100` a call. Both sides share one id sequence in a private chat, so
the range takes older messages and commands too. Messages after that last user message stay,
including the Advisor's reply, Reminders and screens; a surviving screen keeps its input session.
The previous Home dashboard is removed separately. With no user dialogue, only the previous
dashboard is removed. A later user message allows the next clear to remove messages preserved
by an earlier one. The range starts no earlier than the oldest
message kept in the last `TELEGRAM_DELETE_WINDOW` of 48 hours: Telegram lets a bot delete nothing
older.

## What stays

The notes of `CONVERSATION_KINDS` — what was said — stay; every other note in the range goes with
its message. The Diary's `read_conversation` reads a whole day from them (DI-READ-016), past the
clear.

`MessageKind.HOME` is in the history vocabulary's `resets`: the window stops at the newest
dashboard, so the Advisor, a routed subagent and the Summary writer read only what came after it,
an older Summary included. A period read — `day_transcript`, which `read_conversation` is — goes past it. The dashboard is
neither a screen kind nor conversation: nothing the owner does takes it out, and the model never
reads it.

## The words under a Value

[motivation.py](../src/safwa/features/home/motivation.py): one `run_mini_session` per Value in
focus, gathered at once, ending in `motivate(text)` of at most `MOTIVATION_MAX_CHARS = 200`. The
context is About me, the open Goal titles, the `MOTIVATION_DONE_ACTIONS = 10` Actions finished
last and the `MOTIVATION_DIARY_ENTRIES = 2` Diary days written last with words, however old —
then the Value, last, so every session after the first reads the same prefix. A session that
fails is logged and its Value shows its name alone. The words are kept nowhere but the dashboard.
