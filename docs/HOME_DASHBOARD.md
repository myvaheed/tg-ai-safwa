# Home dashboard

Home is one dashboard in two places. `/start` and every `↩️ Menu` draw it as a screen with the
whole menu under it (HM-START-011). When the owner does nothing in the chat for the Profile's
quiet time, or sends `/clear`, Safwa clears messages through the last message the owner sent to
the Advisor, inclusive. Messages after it stay, and a silent Home dashboard is added, built from
the workspace at that moment, with one `☰ Menu` button. The conversation Safwa reads starts over
after it. The rules are HM-QUIET-003 to HM-CLEAR-012
in [home.feature](../tests/brd/home.feature) and PS-HOME-018 in
[profile.feature](../tests/brd/profile.feature); what a Home message does to the chat is the
shell's, TG-HOME-023 in [telegram_history.feature](../tests/brd/tg_agent_shell/telegram_history.feature),
and the check that draws it is a hook on a schedule, AG-HOOK-049 and AG-HOOK-050 in
[agents.feature](../tests/brd/tg_agent_shell/agents.feature).

## How it looks

```text
🏠 Sat, 27 Sep

☀️ Today · 5 of 8
Planned: 8 Actions
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
  The shown/total heading counts Card rows for pagination. Today/Sprint also show planned
  execution counts and, with EP on, their load; repeating rows show × quantity. Today's
  count uses its local day, Sprint uses its dates (the next Sprint's length while Planning).
- **Values in focus**, by name, each with the words written for it, or its name alone when
  none could be written.
- **Time tracked today**, only while Time tracking is on: `tracked_mins` of the Actions finished
  today, workspace local day (`tracked_between`).
- **The last `HOME_LOG_SHOWN = 10` changes**, newest first: when, an icon, the item, what
  changed. An item deleted at or after that change is its title without a link, because SQLite
  hands its id to the next item.

Every item goes through `render_citations`, so it reads and links exactly as a citation in an
answer does.

## The menu under it

[telegram.py](../src/safwa/features/home/telegram.py) draws it. `render_home` is the screen of
`nav:home`, so `/start`, `↩️ Menu` and `go_back` with nowhere to go all reach it. It draws the
dashboard with `menu_markup` under it, as a `MessageKind.DASHBOARD` screen that the next
navigation replaces. It opens at once, with the words under the Values only when fresh ones are
kept; otherwise the words are asked for while it is shown and put in when they come (see
[The words under a Value](#the-words-under-a-value)).

A cleared chat's dashboard carries `home_markup`, one `☰ Menu` that is `nav:home` too. The
shell's `navigation` sees the press is on a `MessageKind.HOME` message and hands it to
`render_home` without ending any screen; `render_home` then only swaps the buttons for the menu
(`ChatHost.set_buttons`), and the words and the note stay. A menu button pressed under it ends
the screens as any navigation does, folds Home back to `☰ Menu`, and hands the screen
`owner_anchor`, so it arrives as a new message below Home instead of replacing it.

## When the chat is cleared

`/clear` (`command_clear`) clears at once: it renders the dashboard and calls `clear_draw_home`
under the background lease, so the owner acting stops it as it stops the clear on a tick. It
does not wait for the quiet time, for the words, or check that anything is owed; its words come
as they do on `/start`.

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

It gets the Values' words first, kept or written, and publishes the dashboard as Markdown of
kind `home`.
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

[motivation.py](../src/safwa/features/home/motivation.py): one `run_mini_session` for every Value
in focus, ending in `motivate(words)`, a list of a Value's number and its text of at most
`MOTIVATION_MAX_CHARS = 200`. The context is About me, the open Goal titles, the
`MOTIVATION_DONE_ACTIONS = 10` Actions finished last and the `MOTIVATION_DIARY_ENTRIES = 2` Diary
days written last with words, however old — then the Values, numbered. A number that names no
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

`/start`, `↩️ Menu` and `/clear` draw at once with `fresh()`. Without fresh words,
`_put_words_in` waits for `write()` in a task the chat spawns, then redraws the message with the
same buttons, under the background lease. It does not when the owner acted after drawing — the
check comes before the lease, so a Home left behind never holds it from a newer one, and the
owner acting cancels the lease after it — or when the message's note no longer holds the text
drawn. The quiet clear waits for `write()` and draws once, as before.
