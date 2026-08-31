# Agents, packet three — nothing queues behind a running answer

Status: **approved 2026-08-30**, held for 8.b step 2, which is the change it describes.
Batch: Phase 8.b. Writes into `tests/brd/agents.feature`, alongside [agents.md](agents.md) and
[agents_interrupted.md](agents_interrupted.md), because one `.feature` file is one package.
Numbering continues from `AG-WORDS-021`.
Sources: the owner's ruling of 2026-08-30; `REFACTORING_CLEAN_ARCH_FINAL.md` §9.3
(`GenerationGuard` as the worked example of a bag of state); current code —
[shell/services.py](../../src/safwa/shell/services.py) (`OwnerAndWritingMiddleware`,
`queue_owner_text`, `GenerationGuard`),
[shell/chat.py](../../src/safwa/shell/chat.py) (`materialize_queued_dialogue`),
[turn/dialogue.py](../../src/safwa/turn/dialogue.py) (`voice_message`).
Supersedes: amends the approved `AG-TURN-010`, see below.

## What the owner changed

**The queue is gone.** Until now, a message written during a running answer was taken out of the
chat and its words held in the process; when the answer finished, everything held went back into
the chat as one message in the owner's name and became a second request. That is replaced by
something simpler to see and simpler to hold:

- while an answer is being written, **one message stands in the chat saying so**, carrying a
  tappable `/cancel`;
- it is removed when the answer arrives, and removed the same way when the owner cancels;
- **anything else the owner sends during that time is taken out of the chat and nothing is done
  with it.**

What this removes, beyond the queue itself: the placeholder per held message, the joining of held
messages into one, the posting-back of that one message, the decode of a voice message that only
existed so its words could be queued, and `Answering.queued` out of the union §9.3 asks for.

## What this packet does not cover

| Rule | Where it already lives |
|---|---|
| Safwa speaks unasked only when nothing of the owner's is open, and the owner wins | `AG-TURN-015` |
| Writing a Summary and keeping memory up to date yield to answering | `CO-GENERATION-011` |
| Words typed over a **screen** continue the request that opened it | `AG-WORDS-016`, `AG-WORDS-017` |
| Owner text still in the chat is what the owner said | `TG-OWNER-003` |
| A spoken message reaches the conversation as words in the owner's name | `TG-RELAY-004` |
| One tool budget per request, however many screens it opens | `AG-BUDGET-011` |

`AG-WORDS-016` is the one worth naming apart, because it looks like a contradiction and is not.
Words typed over an **open review screen** still resume the request that opened it: there the owner
is answering something, and their answer is the point. Words typed while an answer is merely being
generated, with no screen waiting, are what this packet drops on the floor.

## The amendment

### AG-TURN-010 — One request at a time

Status: **approved, amended 2026-08-30 by the owner.** The identifier and the question are
unchanged — one request at a time, and what becomes of what arrives during one. The answer to the
second half changes: held and joined becomes taken out of the chat and dropped.

Amended text, replacing the approved one in `tests/brd/agents.feature`:

```gherkin
  Scenario: AG-TURN-010 — One request at a time, and nothing that arrives during one joins it
    Given Safwa is working on a request
    When the owner writes another message
    Then it does not start a second request
    And it is taken out of the chat, and nothing is done with it
    When the owner sends a recording instead
    Then that is taken out of the chat too, and never transcribed
    When the owner presses a button on any screen
    Then it does nothing while the request is running
    When the owner runs /cancel
    Then the running request is stopped, and that is the one thing they can always do
```

## New scenarios

### AG-TURN-022 — While an answer is being written, the chat says so and offers to stop it

Status: draft — **describes behaviour that does not exist yet**
Sources: the owner's ruling of 2026-08-30
Supersedes: the placeholder written by `queue_owner_text` (`"Generating response... /cancel for
cancelling. Queued: …"`), which appeared once per held message and only when one was held

```gherkin
  Scenario: AG-TURN-022 — While an answer is being written, the chat says so and offers to stop it
    Given the owner asked Safwa something
    When Safwa begins writing the answer
    Then one message stands in the chat saying the answer is being written, offering /cancel as
      something they can tap
    And there is one of those however much the owner sends meanwhile
    When the answer arrives
    Then that message is taken out of the chat
    When the owner cancels instead
    Then it is taken out of the chat the same way, and no answer is written
    And it is never part of the conversation
```

### AG-TURN-023 — Words Safwa could not take out of the chat are answered rather than left hanging

Status: draft
Sources: `_core.py` — the middleware branch for a deletion Telegram refuses; `TG-OWNER-003`, which
makes this the only consistent outcome
Supersedes: none

Taking the message out of the chat **is** what makes it not part of the conversation —
`TG-OWNER-003`. So when Telegram refuses the deletion, the words are already something the owner
said, and leaving them would put a line in the conversation that Safwa never answered.

```gherkin
  Scenario: AG-TURN-023 — Words Safwa could not take out of the chat are answered rather than left hanging
    Given Safwa is working on a request
    When the owner writes, and Telegram refuses to let Safwa take that message out of the chat
    Then the running request is stopped, and what they wrote is answered now
```

## Audit table

| Scenario | Existing tests | Class | Decision | New tests | Status |
|---|---|---|---|---|---|
| AG-TURN-010 | the queue tests in `tests/test_telegram_item_ui.py` and `tests/e2e/test_advisor_flow_e2e.py` | contradictory after the amendment | delete the ones that assert holding and joining, listed by name in 8.a | one for text dropped, one for a recording dropped without a transcription | draft |
| AG-TURN-022 | — | missing | write | one adapter test over answer-then-remove, one over cancel-then-remove | draft |
| AG-TURN-023 | — | missing | write | one adapter test where the deletion is refused | draft |

## What this deletes in 8.b

`QueuedMessage`, `GenerationGuard.begin_queue` / `finish_queue` / `abort_queue` / `drain_queue`,
`queue_messages` as a field, `queue_owner_text`, `materialize_queued_dialogue`, the queue loop in
`run_dialogue_turn`, and `QUEUE_PREVIEW_CHARS`. `send_owner_turn` stays: a transcription still
reaches the conversation through it (`TG-RELAY-004`).

`voice_message` loses its "the lease is settled only now" branch entirely — with nothing to queue,
a recording that arrives during a running answer is refused by the middleware before it is
downloaded, which is also what stops Safwa paying to transcribe something it will discard.
