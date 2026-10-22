# Home dashboard

`/start` opens Home with one `☰ Menu` button. Pressing it or `↩️ Menu` shows the full menu.
Navigation updates the current screen; a new command or message to the Advisor dismisses it.

## Dashboard

Home shows the local date and time, followed by:

- **Latest changes:** the last five changes, newest first. Deleted items have no link.
- **Values in focus:** Values linked to Cards in Today, including their Goals and Subgoals,
  or to scheduled Checks that still need an answer today. Values follow their priority order,
  with a short motivation when available.
- **Today:** every Action under its Goals and Subgoals, with planned execution counts and,
  when enabled, Effort Points. A Card under several Goals appears once; `↖` links to the
  others. Empty Today is shown as empty, without substituting Sprint or Backlog.
- **Checks today:** scheduled Checks with answers left on the local day, including unanswered
  appointments earlier today. Each appears once, linked to its current instance, with the
  remaining count when greater than one. The block is absent when none remain.
- **Time tracked today:** time recorded on Actions finished on the local day, only while
  Time tracking is on.

Items are links that open their screens. The common Card order and Goal tree rules are in
[DOMAIN.md](DOMAIN.md).

## Motivation

Profile → **Motivator instructions** sets the owner's preferred style and emphasis; `off`
clears it. The system prompt contains the main instructions first, then these instructions.

The context contains About me, the local date and time, the running Sprint's Success criteria
(or an explicit absence of a Sprint), Today under its Goals and Subgoals with priorities,
scheduled Checks remaining today, the five latest finished Actions, and the latest Diary day
with text and its date. Each Value has its description, related Cards and Checks, and its
last successful motivation. Cards and Checks may be mentioned only for their linked Values.

Generation uses groups of up to three Values, at most two requests at once, with reasoning
off. Each text is one or two sentences, at most 200 characters. A failed group leaves its
Values with names alone and keeps the other groups' successful texts.

Texts are reused for ten minutes. Expiry does not start a request: the next Home opening
does. Changing the plan does not invalidate these texts; a newly focused Value may show only
its name until the next generation. Previous successful motivation is retained per Value
until restart, including across a failed generation.

Home opens immediately. When fresh texts are unavailable, generation runs in the background
and edits the same message with the same buttons if that Home is still current and the owner
has not acted. Leaving Home does not cancel generation; its results remain available for
the next opening.

## Chat cleanup

After the Profile's **Home after** quiet time (30 minutes by default), a free chat is cleared.
An active turn or pending review postpones cleanup; an empty unprotected chat needs none.
Automatic cleanup preserves unanswered messages Safwa sent on its own after the owner last
wrote to the Advisor. With Secret word set, it also locks access.

`/clear` clears immediately, including those unanswered messages, without locking access.
Neither kind of cleanup opens Home or generates motivation.

After clearing, the Advisor reads the conversation after the clear boundary. Kept dialogue
remains available to the Diary for reading a whole day. Replacing Home does not restore old
conversation. Access and deletion rules are in [CHAT_ACCESS.md](CHAT_ACCESS.md); conversation
reading is described in [LLM_HISTORY.md](LLM_HISTORY.md).

## Code and contracts

- [telegram.py](../src/safwa/features/home/telegram.py): opening Home and updating its message.
- [dashboard.py](../src/safwa/features/home/dashboard.py): dashboard content.
- [motivation.py](../src/safwa/features/home/motivation.py): context, generation and retained texts.
- [hooks.py](../src/safwa/features/home/hooks.py): quiet cleanup.
- [home.feature](../tests/brd/home.feature), [values.feature](../tests/brd/values.feature) and
  [profile.feature](../tests/brd/profile.feature): approved behavior and settings.
