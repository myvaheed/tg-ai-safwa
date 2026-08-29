Feature: Agents — the session, the hand-over, and what comes back

  Safwa answers the owner with one voice and more than one session. The part that talks to the
  owner reads everything and changes nothing; the part that changes something is handed the turn,
  reads the same conversation, and hands back a receipt. A session survives being paused on a
  screen, carries one budget however long it takes, and ends when the process does.

  Approved 2026-08-29. Packets: docs/brd/agents.md and docs/brd/agents_interrupted.md.

  Scenario: AG-ROUTE-001 — A change is written by the part that owns it, and Safwa itself writes none
    Given the owner asks Safwa to change something on their board
    When Safwa works on it
    Then the change is prepared by the subagent that owns that area
    And the part answering in the chat has no way to change anything itself, in any area, so a
      change is always someone else's work
    When the owner instead asks a question about anything in their workspace, the Diary included
    Then that same part answers it from its own reading, without handing the turn to anyone

  Scenario: AG-ROUTE-002 — The hand-over carries a name, never a retelling
    Given the owner wrote a request in their own words
    When the turn is handed to a subagent
    Then the subagent is given the name of its area and no retelling of the request
    And it reads the same recent conversation, so the owner's exact words are what it works from

  Scenario: AG-ROUTE-002 — The conversation reaches the subagent as data, not as its own voice
    Given the conversation the subagent is handed
    When it reads it
    Then the newest 10 messages arrive as one block, each tagged with who wrote it
      (SUBAGENT_HISTORY_LAST_MESSAGES = 10)
    And none of them arrives as if the subagent had said it

  Scenario: AG-ROUTE-003 — A request that names two areas is one request, not two
    Given the owner asks for one thing on their board and one thing in their Diary, in a single
      message
    When Safwa works on it
    Then each area is handed to the subagent that owns it, one after the other, within the same
      request
    And the request is not finished until both have been dealt with

  Scenario: AG-ROUTE-004 — The part doing the work cannot hand the work on again
    Given a subagent is doing the work it was handed
    When it looks for a way to hand that work on to another subagent
    Then it has none, so the chain is never more than two deep

  Scenario: AG-ROUTE-005 — Handing the turn over is the only thing Safwa does in that step
    Given Safwa decides to hand the turn to a subagent
    When it tries to do something else in the same step
    Then nothing in that step runs, Safwa is told to hand over on its own, and it tries again

  Scenario: AG-RECEIPT-006 — Only Safwa writes to the chat
    Given a subagent finished the work it was handed and has something to say about it
    When the request ends
    Then its words went back to the part answering in the chat, never into the chat themselves
    And that part writes the single message the owner reads, keeping the links to any item named

  Scenario: AG-RECEIPT-007 — Work that breaks comes back as a report, not as a silence
    Given a subagent fails part-way through the work it was handed
    When the failure happens
    Then what comes back is a short account of the failure, in place of a result
    And Safwa still answers the owner in the same request, telling them what did not happen

  Scenario: AG-SESSION-008 — Work paused on a screen picks up where it stopped
    Given a subagent prepared a change and its screen is waiting in the chat
    When the owner saves it, minutes or hours later
    Then the subagent carries on from the step it stopped at, with everything it had already worked
      out
    And its result then reaches the part that handed it the work, which answers the owner

  Scenario: AG-SESSION-008 — What it knows about the workspace is read fresh, not replayed
    Given the subagent stopped for the owner
    When it starts again
    Then what it knows about the board and the time of day is read fresh, not replayed from when it
      stopped

  Scenario: AG-SESSION-009 — A restart ends every piece of work that was waiting
    Given work was paused on a screen, or was running when the process stopped
    When Safwa starts again
    Then none of it is picked up, and the owner starts from a request they make now
    And no button left in the chat can revive it

  Scenario: AG-TURN-010 — One request at a time, and words that arrive during one join it
    Given Safwa is working on a request
    When the owner writes another message
    Then it does not start a second request
    And it is held, and taken up as part of the same request once the running one finishes
    When the owner presses a button on any screen instead
    Then it does nothing while the request is running
    When the owner runs /cancel
    Then the running request is stopped, and that is the one thing they can always do

  Scenario: AG-BUDGET-011 — One request has one tool budget, however many screens it opens
    Given a request that opens several screens before it is finished
    When the owner answers each of them and the work carries on
    Then every step it has taken counts against one budget of 64 (MAX_TOOL_CALLS = 64)
    And the budget is not refilled by a pause, so a request that never settles is stopped

  Scenario: AG-BUDGET-012 — A subagent that takes too long is stopped by the clock
    Given a subagent is working while the owner waits for an answer
    When it has been working for 300 seconds without finishing (SUBAGENT_DEADLINE_SECONDS = 300)
    Then it is stopped, and the owner is told it did not finish
    When instead a screen it opened is waiting for the owner
    Then the time the owner takes to decide does not count against that, however long they take

  Scenario: AG-ANSWER-013 — A step that produces nothing is asked again, and the owner is never left with nothing
    Given a step ends with neither an answer nor anything to do
    When that happens
    Then Safwa is told it stopped without answering, and asked again, up to 5 times
      (MAX_REPAIR_ROUNDS = 5)
    And if it still has nothing, the owner is told so in one line rather than left with silence

  Scenario: AG-ANSWER-014 — A subagent's first step is always the work
    Given a subagent has just been handed a turn
    When it takes its first step
    Then it has to do something, not answer in words — it was handed the turn for the work
    And every step after that is free to be the answer, or the work would never finish

  Scenario: AG-TURN-015 — Safwa speaks unasked only when nothing of the owner's is open
    Given the system has something for Safwa to say without being asked, such as a Reminder coming
      due
    When a request of the owner's is running, or a screen of theirs is waiting for a decision
    Then Safwa says nothing, and what it owes them is still owed
    When nothing of the owner's is open
    Then it is said as one ordinary answer, and only then is it no longer owed
    When the owner writes while Safwa is part-way through saying it
    Then the owner wins, the half-written message is thrown away, and it is still owed

  Scenario: AG-WORDS-016 — Words typed over a screen continue the request that opened it
    Given a subagent proposed a change and its screen is waiting in the chat
    When the owner writes a message instead of pressing Save or Discard
    Then the request that opened that screen carries on and answers those words
    And it is not thrown away and started again from nothing
    And the part answering in the chat is what reads them, before anything is handed anywhere

  Scenario: AG-WORDS-017 — The resumed request is told the whole of what happened
    Given a request that had handed the turn to a subagent and is waiting on its result
    When the owner writes over the screen that subagent opened
    Then the waiting request is given what was proposed, what was refused, what was already saved,
      and what the owner wrote
    And it answers from those four things together, so a half-finished request is not reported as
      done

  Scenario: AG-WORDS-018 — A correction reaches the subagent that wrote the refused proposal
    Given a subagent proposed a Diary entry and the owner refused it by writing "the same, but
      shorter"
    When Safwa hands the turn back to that subagent
    Then the same subagent picks the work up, still holding the entry it had written
    And what it proposes next is that entry corrected, not a new one written from scratch

  Scenario: AG-WORDS-019 — The subagent's own record says the owner refused and wrote instead
    Given a subagent's proposal was refused by words the owner typed
    When it picks the work up again
    Then its own record of the work says both that the proposal was refused and that the owner
      wrote something instead of deciding
    And it does not propose the same thing a second time

  Scenario: AG-WORDS-020 — Unfinished work ends when the request that started it ends
    Given a subagent was interrupted and its work was left unfinished
    When the request that had handed it the turn finally answers the owner, or fails
    Then that unfinished work is ended right there
    And nothing from it can be picked up by a later request

  Scenario: AG-WORDS-021 — Saving finishes the subagent, so asking it again starts it fresh
    Given the owner saved a change a subagent proposed, and that subagent finished and reported back
    When the same request hands the turn to that same subagent again
    Then it starts from nothing, with no memory of the change already saved
    And it works from the conversation, where the saved change is already visible
