# The conversation in Telegram, packet one — what Safwa reads back

Status: **approved 2026-08-30**, all eleven written into `tests/brd/telegram_history.feature` — ten in 8.a step 2, `TG-NOTES-007` in step 3 with the change it describes.
Batch: Phase 8.a. Writes into `tests/brd/telegram_history.feature` (new file, prefix `TG`).
Sources: `CLAUDE.md` §"Telegram is the canonical dialogue store, not SQLite";
`REFACTORING_CLEAN_ARCH_FINAL.md` §13.2 (the four invariants of the UI core) and §13.3 (what goes
into `telegram_llm` and what stays in Safwa); current code — [history.py](../../src/safwa/history.py)
(`recent`, `dialogue`, `mark_message`, `read_message_mark`, `restore_citations`,
`split_receipts`, `_registered_message`, `register_message`),
[shell/chat.py](../../src/safwa/shell/chat.py) (`send_registered`,
`send_owner_turn`, `dismiss_prior_ui`),
[shell/services.py](../../src/safwa/shell/services.py) (`OwnerAndWritingMiddleware`),
[turn/dialogue.py](../../src/safwa/turn/dialogue.py) (`voice_message`);
`archived_docs/MEMORY_HISTORY_USAGE.md`, read for intent and checked against the code.
Supersedes: named per scenario.

## What "a note" means in this packet, and what it is not

Safwa keeps **no copy of the conversation**. Every turn it re-reads the real private chat through
a Telethon user session. `telegram_messages` is a table of **notes about messages**, never their
text: for each message Safwa sent or read, what kind of message it was, which item it is about, and
an identifier that survives editing it. Its readers are almost all about screens, not history:

| Reader | What the note is for |
|---|---|
| `dismiss_prior_ui` | find every screen still open, to close all but the one being acted on |
| `send_registered`, `edit_registered_message` | keep a screen's identity while redrawing it in place |
| `delete_screen`, `discard_stale_status` | know what is still standing in the chat after a restart |
| `recovery` | clear the item a review screen pointed at, once the process that owned it is gone |
| `plan.py` | confirm a message id really is a dashboard before redrawing it |
| `cues/runtime` | confirm a Cue actually reached the chat before the row is deleted |

None of the six can be answered out of Telethon — they are asked on every button press — and none
of them is going anywhere.

**Reading the conversation is not on that list, and after 8.b it does not use the notes at all.**
It used them for three things, and the owner removed all three on 2026-08-30: matching the two id
spaces, reading an incoming message's kind, which `TG-OWNER-003` settles without a note, and
refusing to read back past the oldest note. What the conversation is read from is the chat, and
nothing else.

## Why this packet exists

Phase 8 moves the window, the marking and the note-keeping out of Safwa and into `telegram_llm`, a
package that may not import Safwa. Nothing about how Safwa reads its own chat has ever been written
down. Eleven rules are currently held by tests whose names are the only statement of them, and by
comments inside the function that implements them.

## What this packet does not cover

| Rule | Where it already lives |
|---|---|
| When a Summary is written, and from what | `CO-SUMMARY-001`, `CO-SUMMARY-002`, `CO-SUMMARY-003` |
| Background work yields to the owner | `CO-GENERATION-011`, `AG-TURN-015` |
| One request at a time, and what becomes of anything that arrives during one | `AG-TURN-010`, `AG-TURN-022` |
| The conversation reaches a subagent as data, not as its own turns | `AG-ROUTE-002` |
| Only Safwa writes to the chat | `AG-RECEIPT-006` |
| Which screen stays live, and what happens to the others | `SC-LIVE-001` |
| A message too long for Telegram arrives in several parts | `SC-SPLIT-004` |
| What Safwa points the owner at, and what an item that is gone reads as | `DI-LINK-007`, `DI-READ-006` |
| Reading one named day from end to end, Summary or no Summary | `DI-READ-016` |

`TG-CITE-011` and `DI-LINK-007` are close enough to be worth naming apart. `DI-LINK-007` is about
what the **owner** gets — a tappable link that opens the day read-only. `TG-CITE-011` is about what
**Safwa** gets when it reads its own answer back a turn later.

## Scenarios

### TG-MARK-001 — A message Safwa sent off the record is not part of the conversation

Status: draft
Sources: `history.py` `mark_message` / `read_message_mark`; `_messaging.py` `send_registered`
Supersedes: `tests/test_history.py::test_unmarked_bot_prose_is_excluded` (business_valid),
`::test_kind_mark_round_trips_and_is_invisible` (business_valid),
`::test_event_marker_survives_message_edits` (business_valid)

```gherkin
  Scenario: TG-MARK-001 — A message Safwa sent off the record is not part of the conversation
    Given a message Safwa put in the chat without saying what kind of message it was
    When Safwa reads the conversation back
    Then that message is not in it
    And on the messages that do say, the marking is invisible to the owner
    And a message Safwa rewrites in place stays the same message, so rewriting a review screen
      into an account of what became of it does not add a second one
```

### TG-KIND-002 — Screens, receipts and progress notes are never part of the conversation

Status: draft
Sources: `history.py` `recent` — the kind filter on Safwa's own messages
Supersedes: none; no test states this as a rule

```gherkin
  Scenario: TG-KIND-002 — Screens, receipts and progress notes are never part of the conversation
    Given a dashboard, a line reporting what a button just did, a progress note and an error
      message were all put in the chat
    When Safwa reads the conversation back
    Then none of the four is in it
    And what is in it is what was said: the owner's words, Safwa's answers, what Safwa said
      unasked, and the Summaries
```

### TG-OWNER-003 — Owner text still in the chat is what the owner said

Status: draft
Sources: `history.py` `recent`, the branch on the owner's own messages; `_core.py`
`OwnerAndWritingMiddleware`, which deletes a command; `telegram/text_input.py`
`delete_text_input`
Supersedes: `tests/test_history.py::test_surviving_owner_text_is_dialogue_down_to_the_oldest_registration`
(business_valid, split — the "how far back" half of it is deleted)

Only Safwa's own messages can carry a marking; the owner's Telegram cannot write one, so nothing
in a message of theirs says whether it was conversation or an instruction. The one thing that
distinguishes them is that Safwa **takes an instruction out of the chat** as it reads it. That
deletion is not tidiness — it is the whole of the classification, and this scenario is what says
so, so that a later change which stops deleting a typed field value does not quietly make it a line
of the conversation.

A command is the one thing that does not rest on the deletion: text opening with `/` is refused
whether or not Telegram let Safwa take it away. The first draft of this scenario had the opposite,
which was wrong — a `/today` left standing after a failed deletion would read as the owner saying
"/today".

```gherkin
  Scenario: TG-OWNER-003 — Owner text still in the chat is what the owner said
    Given a message the owner sent themselves, carrying no marking of any kind
    When Safwa reads the conversation back
    Then it counts as something they said
    And a command, or a value they typed into a field, is not found at all — Safwa took each of
      those out of the chat as it read them
    When a command Safwa failed to take out is left standing in the chat
    Then it is still not part of the conversation, because text opening with "/" never is
```

### TG-RELAY-004 — Words that never reached the chat as the owner's are put there as theirs

Status: draft
Sources: `_messaging.py` `send_owner_turn`; `dialogue.py` `voice_message`
Supersedes: none; the posting-back is asserted inside E2E flows, never as a rule.
The queued-words half of this rule was deleted by the owner on 2026-08-30 — see
[agents_turn.md](agents_turn.md).

One case is left, now that nothing queues: a voice message. The audio carries no text, so those
words never reached the chat, and a conversation read out of the chat would not have them. Safwa
puts them there itself, in the owner's name.

```gherkin
  Scenario: TG-RELAY-004 — Words that never reached the chat as the owner's are put there as theirs
    Given the owner spoke instead of typing, so what they said is in the chat as a recording and
      not as words
    When Safwa has turned it into words
    Then it puts them in the chat as a message in the owner's name, before answering them
    And the conversation reads them back as something the owner said
```

### TG-WINDOW-005 — How much conversation Safwa reads is a size, not a number of messages

Status: draft
Sources: `history.py` `recent`, the budget check before an entry is taken
Supersedes: `tests/test_history.py::test_the_window_is_cut_on_a_message_boundary_when_the_budget_runs_out`
(business_valid)

```gherkin
  Scenario: TG-WINDOW-005 — How much conversation Safwa reads is a size, not a number of messages
    Given a conversation longer than Safwa reads at once
    When Safwa reads it back before answering
    Then it takes the newest messages until 6000 tokens are spent
      (SUMMARY_TRIGGER_TOKENS = 6000)
    And the cut falls between two messages, so no message arrives half there
    And a hundred short messages may all fit where three long ones do not
```

### TG-SUMMARY-006 — The newest Summary is where the window ends

Status: draft
Sources: `history.py` `recent` — the Summary boundary and `summary_context`
Supersedes: `tests/test_history.py::test_summary_is_pinned_first_with_twenty_prior_messages`
(business_valid)

A Summary is skipped rather than treated as an edge for exactly one reader, the Diary reading a
named day. The owner ruled on 2026-08-30 that this is a Diary rule and not a property of the
window; it is `DI-READ-016`, in [diary_day_read.md](diary_day_read.md).

```gherkin
  Scenario: TG-SUMMARY-006 — The newest Summary is where the window ends
    Given the conversation has a Summary in it
    When Safwa reads the conversation back before answering
    Then it stops at the newest Summary and reads that in place of what came before it
    And an older Summary is never read
    And up to 20 of the messages just before it come along with it
      (SUMMARY_CONTEXT_MESSAGE_LIMIT = 20)
```

### TG-NOTES-007 — The conversation is read from the chat, and from nothing Safwa keeps

Status: draft — **a behaviour change the owner asked for**: the "read back no further than the
oldest note" rule is deleted
Sources: `history.py` `recent` — the floor, deleted in 8.a step 3, and the fall-back to markings
Supersedes: `tests/test_history.py::test_owner_text_older_than_every_registration_is_excluded`
(contradictory after the change, **deleted**),
`::test_kind_marks_rebuild_history_without_registrations` (business_valid)

Safwa refused to read the chat from before its own oldest note. It guarded one case — this chat
held a conversation before Safwa was in it — which a fresh bot does not have and `qa.py` already
refuses to create. The owner dropped it on 2026-08-30. What stops the read is what always did the
work anyway: the token budget, and the newest Summary.

```gherkin
  Scenario: TG-NOTES-007 — The conversation is read from the chat, and from nothing Safwa keeps
    Given every note Safwa kept about this chat is gone
    When Safwa reads the conversation back
    Then the marking on its own messages still says what each of them was
    And the owner's surviving messages still count as theirs
    And the conversation reads as it did
    And what stops the read is the token budget, or the newest Summary — never a note
```

### TG-CURRENT-008 — The message Safwa is answering is in the conversation exactly once

Status: draft — **the mechanism behind it is deleted in 8.b**, at the owner's instruction on
2026-08-30; the rule itself does not change
Sources: `history.py` `recent` (`source_message`) and `_registered_message`
Supersedes: `tests/test_history.py::test_current_source_is_not_duplicated_across_telegram_id_spaces`
(business_valid), `::test_private_chat_correlates_telethon_and_bot_api_message_ids`
(implementation_coupled, **deleted** — it tests the matching, and the matching goes),
`::test_colliding_id_space_does_not_drop_the_newest_dialogue_message` (business_valid)

The Bot API and a Telethon user session number the same message differently in a private chat, so
the message being answered cannot be recognised by its number. Today about sixty lines in
`_registered_message` reconcile the two by when the message was sent, to within 15 seconds, and
that code does two jobs: read an incoming message's kind off its note, and avoid counting the
message being answered twice. `TG-OWNER-003` already settles the first — surviving owner text is
the conversation whatever a note says. The second needs only what Safwa already holds: the words it
is answering, added when the chat read did not come back with them, matched on the words and the
minute. `_registered_message` and `MESSAGE_CORRELATION_SECONDS` are deleted in 8.a step 3.

Two identical messages sent inside one minute are the case this gets wrong, and both would have to
arrive during the one turn.

```gherkin
  Scenario: TG-CURRENT-008 — The message Safwa is answering is in the conversation exactly once
    Given the owner just sent a message and Safwa is answering it
    When Safwa reads the conversation back
    Then that message is in it once — never twice, and never missing
```

### TG-RECEIPT-009 — A line reporting what was saved is not something Safwa said

Status: draft
Sources: `history.py` `split_receipts`; `features/proposals/model.py` `RECEIPT_MEANINGS`
Supersedes: `tests/test_history.py::test_a_receipt_reads_back_as_a_tool_result_rather_than_as_safwa_words`
(business_valid)

This is what becomes a `[Tool result]` line. Safwa's answer to the owner ends with lines such as
"✅ Saved — …"; read back a turn later, each of those becomes a tool result attached to the request
it answered, and only the rest of the message is Safwa's voice.

```gherkin
  Scenario: TG-RECEIPT-009 — A line reporting what was saved is not something Safwa said
    Given Safwa's answer carried lines saying what an approved change did
    When Safwa reads that answer back a turn later
    Then those lines come back as the outcome of the work, placed with the request they answered
    And what is left of the message is what Safwa actually said to the owner
```

### TG-SHAPE-010 — The conversation is laid out as turns, not as messages

Status: draft
Sources: `history.py` `dialogue` — the user-block flush, the assistant merge, and the per-hour stamp
Supersedes: `tests/test_history.py::test_dialogue_groups_every_user_message_until_the_next_ai_response`
(business_valid)

```gherkin
  Scenario: TG-SHAPE-010 — The conversation is laid out as turns, not as messages
    Given a stretch of conversation holding the owner's words, the outcomes of work Safwa did, and
      the answers Safwa gave
    When Safwa reads it back
    Then everything that is not an answer of Safwa's gathers into one turn of the owner's, up to
      the next answer
    And answers with nothing between them are read as one answer
    And the time is written once for each hour of conversation, not once per message
```

### TG-CITE-011 — A link Safwa wrote reads back as the citation it wrote

Status: draft
Sources: `history.py` `restore_citations`, `CITATION_TYPES`
Supersedes: `tests/test_history.py::test_item_links_read_back_as_the_citations_the_model_wrote`
(business_valid), `::test_restore_citations_only_rewrites_safwa_deep_links` (business_valid),
`::test_a_compact_diary_label_round_trips_as_a_citation` (business_valid)

```gherkin
  Scenario: TG-CITE-011 — A link Safwa wrote reads back as the citation it wrote
    Given Safwa's answer pointed the owner at a Card, and they see a tappable link
    When Safwa reads that answer back a turn later
    Then the link reads as the citation Safwa originally wrote, not as bare words
    And a link to anything that is not one of Safwa's own items is left exactly as it is
```

## Audit table

| Scenario | Existing tests | Class | Decision | New tests | Status |
|---|---|---|---|---|---|
| TG-MARK-001 | `test_unmarked_bot_prose_is_excluded`, `test_kind_mark_round_trips_and_is_invisible`, `test_event_marker_survives_message_edits` | business_valid | cite | — | draft |
| TG-KIND-002 | — | missing | write | one adapter test over a chat holding all four kinds | draft |
| TG-OWNER-003 | `test_surviving_owner_text_is_dialogue_down_to_the_oldest_registration` | business_valid | split with TG-START-007, cite | one for the command left standing | draft |
| TG-RELAY-004 | E2E only, incidentally | missing | write | one adapter test for the spoken word | draft |
| TG-WINDOW-005 | `test_the_window_is_cut_on_a_message_boundary_when_the_budget_runs_out` | business_valid | cite | — | draft |
| TG-SUMMARY-006 | `test_summary_is_pinned_first_with_twenty_prior_messages` | business_valid | cite | — | draft |
| TG-NOTES-007 | `test_owner_text_older_than_every_registration_is_excluded`, `test_kind_marks_rebuild_history_without_registrations` | contradictory / business_valid | delete the first, cite the second | one asserting a read that reaches past every note | draft |
| TG-CURRENT-008 | `test_current_source_is_not_duplicated_across_telegram_id_spaces`, `test_colliding_id_space_does_not_drop_the_newest_dialogue_message` | business_valid | cite; delete `test_private_chat_correlates_telethon_and_bot_api_message_ids` with the matching | one over a chat read that has not caught up yet | draft |
| TG-RECEIPT-009 | `test_a_receipt_reads_back_as_a_tool_result_rather_than_as_safwa_words` | business_valid | cite | — | draft |
| TG-SHAPE-010 | `test_dialogue_groups_every_user_message_until_the_next_ai_response` | business_valid | cite; the assistant merge and the stamp are new | two | draft |
| TG-CITE-011 | `test_item_links_read_back_as_the_citations_the_model_wrote`, `test_restore_citations_only_rewrites_safwa_deep_links`, `test_a_compact_diary_label_round_trips_as_a_citation` | business_valid | cite all three | — | draft |

`test_conversation_block_tags_every_line_by_who_wrote_it` and
`test_conversation_block_is_empty_when_the_conversation_is` are not in this packet: they belong to
`AG-ROUTE-002`, which is approved, and they are cited there in 8.a rather than rewritten.

## What 8.a step 3 deleted, on the owner's two rulings

`_registered_message` and `MESSAGE_CORRELATION_SECONDS`; the floor in `recent`; and the reading of
an incoming message's kind off its note. With all three gone, `register_message` is not called for
the owner's own messages at all, and `recent` reads only the notes on Safwa's own outgoing
messages — which is what their six remaining readers ask about.

**How the answered message is recognised, now that there is no correlation.** Two cases, and they
are different rather than two mechanisms for one thing. Words Safwa posted on the owner's behalf —
a transcript — carry a note it wrote itself, so its event id is an exact match. The owner's own
typed message carries no note at all, so the words and the minute are what identify it. Two
identical messages inside one minute is the case that gets wrong, and both would have to arrive
during the one turn.
