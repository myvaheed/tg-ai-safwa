Feature: The durable facts about the owner
  Safwa remembers durable facts in memory.md — a plain text file the owner owns and may edit in any
  editor. Telegram is where the conversation really lives; that file is where the facts really live.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given memory.md, a plain text file the owner may edit

  Scenario: MEM-FILE-001 — memory.md is where the facts are
    Given memory.md
    When Safwa reads what it remembers
    Then every non-empty line is one fact, in the order the file has them
    And blank lines are skipped
    And a file that is not there means Safwa remembers nothing, which is not a failure

  Scenario: MEM-FILE-002 — An edit the owner makes themselves is picked up on the next read
    Given Safwa has already read memory.md
    When the owner edits the file outside Safwa
    Then the next read gives Safwa what the owner wrote

  Scenario: MEM-FILE-003 — Safwa never writes over an edit the owner made in the meantime
    Given Safwa started rewriting memory from the file as it stood
    When the owner edits that file before the rewrite is saved
    Then the rewrite is dropped rather than saved over them
    And the owner's file is left exactly as they left it

  Scenario: MEM-SYNC-004 — Memory reads on from where it last stopped
    Given memory was last brought up to date to a point in the conversation
    When it is brought up to date again
    Then it reads only what was said after that point, never the whole conversation again

  Scenario: MEM-SYNC-005 — Memory moves its place on only after the file was actually written
    Given there is conversation memory has not read yet
    When the owner's own edit means the rewrite cannot be saved
    Then the owner's file is what survives
    And memory has not moved its place on, so that same conversation is read again next time

  Scenario: MEM-SCHEDULE-006 — Memory upkeep happens once a day, however often it is checked
    Given the owner set a time of day for memory upkeep, and today's run has not happened yet
    When that time has passed and the check runs several times over the day
    Then upkeep happens once, not once per check

  Scenario: MEM-SCHEDULE-007 — Off means memory upkeep does not happen at all
    Given the owner set memory upkeep to off
    When the check runs
    Then nothing is read, nothing is written, and nothing is sent to the model

  Scenario: MEM-FILE-008 — A memory.md Safwa cannot use is never written over
    Given memory.md is not text Safwa can read, or is over 4000 tokens (MEMORY_TOKEN_BUDGET = 4000)
    When Safwa needs what it remembers
    Then it answers with no facts rather than failing the turn, and records why
    And scheduled upkeep stops instead of sending that file to the model
    And a fact the owner adds with /mem is refused and told why
    And the owner's file is left byte for byte as it is, because Safwa does not write over what it
      could not read

  Scenario: MEM-FILE-009 — A fact the owner adds by hand goes to the end of the file
    Given memory.md already holds two facts
    When the owner adds one with /mem
    Then the file holds the two it had, and the new one after them
