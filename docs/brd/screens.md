# Screens, packet one — what a screen is, and what a button is

Status: **approved 2026-08-30**, all five written into `tests/brd/screens.feature` — `SC-FAIL-005` at 8.a step 1, `SC-KEEP-002` at step 2, `SC-SPLIT-004` at step 4 and `SC-BUTTON-003` at step 5, each with the change it describes.
Batch: Phase 8.a. Writes into `tests/brd/screens.feature`, which already carries `SC-LIVE-001`.
This packet does not touch `SC-LIVE-001`; it was approved with
[agents_interrupted.md](agents_interrupted.md) and [proposals_interrupted.md](proposals_interrupted.md)
and stands as it is.
Sources: `REFACTORING_CLEAN_ARCH_FINAL.md` §13.2 invariants three and four, §13.3; current code —
[shell/chat.py](../../src/safwa/shell/chat.py) (`token_button`, `send_toast`,
`_expire_toast`, `delete_screen`, `send_registered`, `send_owner_turn`, `send_summary`),
[shell/callbacks.py](../../src/safwa/shell/callbacks.py) (`callback_token_handler`),
[shell/layout.py](../../src/safwa/shell/layout.py) (`split_telegram_text`),
[recovery.py](../../src/safwa/recovery.py), [ai/materialize.py](../../src/safwa/features/proposals/materialize.py)
(`open_batch`), [turn/dialogue.py](../../src/safwa/turn/dialogue.py) (`run_dialogue_turn`).
Supersedes: named per scenario.

## Why this packet exists

Phase 8 moves the screen lifecycle and the button into `telegram_llm`. Three of the four invariants
§13.2 names are about this file, and only one of them — which screen stays live — has ever been
written down.

Two of the four scenarios below are **behaviour changes the owner asked for on 2026-08-30**, not
descriptions of the code: `SC-BUTTON-003` replaces a button's 24-hour lifetime with the life of the
process, and `SC-SPLIT-004` makes splitting a property of every outgoing message. `SC-FAIL-005` is
a defect the Phase 7 review found and handed to Phase 8, and the owner asked for it to be fixed as
early as possible — it is now the first thing 8.a does.

## What this packet does not cover

| Rule | Where it already lives |
|---|---|
| Which screen stays live when the owner acts elsewhere | `SC-LIVE-001` |
| A review screen freezes into an account of what became of it | `SC-LIVE-001` |
| A button does nothing while a request is running | `AG-TURN-010` |
| A review screen is exactly Save and Discard, and what each writes | `PR-SCREEN-003`, `PR-SAVE-009`, `PR-SAVE-010` |
| A proposal lives for one running process | `PR-STALE-013` |
| A rejected answer to a typed field changes nothing and asks again | `PS-UI-INVALID-009` |

`PR-STALE-013` and `SC-BUTTON-003` are close enough to name apart. `PR-STALE-013` is about the
**proposal**: a change prepared in one run of Safwa cannot be saved by the next one. `SC-BUTTON-003`
is about **every** button, of which a proposal's Save is one case — after the change below, the two
rules say the same thing about the same lifetime, and a proposal button stops being an exception.

## Scenarios

### SC-KEEP-002 — What Safwa puts in the chat stays, is replaced, or takes itself away

Status: draft
Sources: `_messaging.py` `send_registered` (edit in place), `delete_screen`, `send_toast` /
`_expire_toast`
Supersedes: none; the toast's own lifetime is asserted nowhere

```gherkin
  Scenario: SC-KEEP-002 — What Safwa puts in the chat stays, is replaced, or takes itself away
    Given Safwa puts three kinds of thing in the chat: what is said, screens the owner acts on,
      and short notes about what just happened
    When the owner carries on
    Then what was said stays in the chat for good
    And a screen is redrawn in place, or taken out of the chat — never left standing beside a
      second copy of itself
    And a short note takes itself out of the chat after 5 seconds (TOAST_SECONDS = 5)
```

### SC-BUTTON-003 — A button works once, and dies with the run of Safwa that drew it

Status: draft — **the second block is a behaviour change the owner asked for**
Sources: `_messaging.py` `token_button`; `callbacks.py` `callback_token_handler`; `recovery.py`
Supersedes: `tests/test_telegram_item_ui.py` — the single-use assertions inside the callback tests
(business_valid, cited rather than rewritten)

Today a button is a one-time record with a 24-hour lifetime that outlives a restart, except a
proposal's buttons, which are deleted at the next start because the review they belong to lived in
the process that made them. The owner ruled that this is the rule for **all** of them: a screen is
a view of state a running Safwa is holding, so a restart makes every screen out of date, and the
24-hour lifetime goes.

The second `When` is not "a screen with no buttons" — a screen Safwa took away has no buttons to
press. It is a screen the owner can still see: a Telegram client that has not caught up, a second
device, or a redraw Telegram refused.

```gherkin
  Scenario: SC-BUTTON-003 — A button works once
    Given a screen with buttons on it
    When the owner presses the same button twice
    Then the work happens once, and the second press is told the action has expired

  Scenario: SC-BUTTON-003 — A button dies with the run of Safwa that drew it
    Given a screen the owner can still see, drawn before Safwa was last restarted
    When they press a button on it
    Then nothing happens to the item it named
    And that screen is replaced by a line saying it is out of date, so nothing answerable is left
      standing
```

### SC-SPLIT-004 — A message too long for Telegram arrives whole, in several parts

Status: draft — **a behaviour change the owner asked for**, settling the question the first draft
of this packet raised
Sources: `_presentation.py` `split_telegram_text`; `_messaging.py` `send_owner_turn` is its only
caller today; `REFACTORING_CLEAN_ARCH_FINAL.md` §13.3 "safe splitting and escaping of HTML"
Supersedes: none

Today only one thing is split: the words Safwa puts in the chat on the owner's behalf. It cuts at a
line break, then a space, then at the limit, and nothing checks that formatting is not left open
across the cut. Everything else is sent whole, so anything longer than Telegram accepts fails to
send — and a Summary is allowed 2000 tokens (`SUMMARY_TOKEN_CEILING = 2000`), which is comfortably
more than 3900 characters, so this is reachable rather than theoretical.

The owner chose: split everything, one send for all of it.

```gherkin
  Scenario: SC-SPLIT-004 — A message too long for Telegram arrives whole, in several parts
    Given Safwa has more to say than 3900 characters fit (TELEGRAM_TEXT_LIMIT = 3900)
    When it puts that in the chat — an answer, a Summary, or the owner's own relayed words
    Then it arrives as several messages in order, and nothing is lost between them
    And the split falls at a line break where there is one
    And no bold, italic or code formatting is left open across a split
    And the conversation reads it back as one turn, not as several
```

### SC-FAIL-005 — A review that could not be put on screen does not stay open

Status: draft — **describes behaviour that does not exist yet**, and is the rule the fix has to
satisfy. Fixed first in 8.a, at the owner's instruction.
Sources: the Phase 7 review, `docs/MIGRATION.md` §"The seam with Phase 8"; `ai/materialize.py`
`open_batch`; `telegram/dialogue.py` `run_dialogue_turn`'s `except Exception`;
`cues/runtime.py` `CueRuntime.speak`, which already guards its own path this way
Supersedes: none

The review is opened before the screen is drawn, and drawing it is the last step. When drawing
fails, the review stays open with nothing on screen; the owner's next words find no screen to
close, so it stays open for the life of the process. Nothing is lost and nothing is said: every
Reminder that comes due and every Sprint that ends waits in silence until a restart. The Cue path
already closes its own review in its failure handler; the owner's path does not, and that
difference is the whole of it.

```gherkin
  Scenario: SC-FAIL-005 — A review that could not be put on screen does not stay open
    Given Safwa prepared a change and the review screen for it could not be put in the chat
    When that happens
    Then the review ends rather than staying open with nothing on screen
    And the owner is told the change was not saved
    And what Safwa was waiting to say unasked — a Reminder that came due, a Sprint that ended — is
      said as soon as nothing of the owner's is open, rather than waiting for a restart
```

## Audit table

| Scenario | Existing tests | Class | Decision | New tests | Status |
|---|---|---|---|---|---|
| SC-KEEP-002 | — | missing | write | one adapter test per outcome: kept, replaced, expired | draft |
| SC-BUTTON-003 | assertions inside `tests/test_telegram_item_ui.py` | business_valid | cite the double-press one; write the restart one against the change | one over a restart, one asserting `CALLBACK_TOKEN_TTL_HOURS` is gone | draft |
| SC-SPLIT-004 | — | missing | write against the change | one over an over-long answer, one over an over-long Summary | draft |
| SC-FAIL-005 | — | missing | write against the fix | one E2E over the owner's path: the screen fails to send, and the next Cue is spoken | draft |

## What the two changes touch

- **`SC-BUTTON-003`.** `CALLBACK_TOKEN_TTL_HOURS` and `CallbackToken.expires_at` go; `recovery`
  deletes every `CallbackToken` at start rather than the expired ones and the proposal ones;
  `PROPOSAL_CALLBACK_ACTIONS` loses its exceptional lifetime and, with it, one of the reasons
  `callbacks.py` reaches into `recovery.py`. `callback_token_handler`'s "This action expired"
  answer keeps its place for a second press, and a press with no token at all replaces the screen
  with an out-of-date line instead.
- **`SC-SPLIT-004`.** One send in `telegram_llm` splits and closes formatting; `send_summary` and
  `send_registered` go through it. `record_summary` names the message the window cuts back to, so
  a split Summary has to name its **last** part, or the window would cut back to the middle of one.

## Note on where these live after Phase 8

`tests/brd/README.md` calls `screens.feature` the one file not owned by a package under
`features/`. Its rules are now kept by `src/safwa/shell` and `telegram_llm`, and this file is their
acceptance. `SC-FAIL-005` is the exception: the review it is about is Safwa's, so the fix is in
Safwa and the scenario stays with the screen it names.
