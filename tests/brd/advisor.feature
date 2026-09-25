Feature: Advisor
  The Advisor is the part of Safwa the owner talks to, and the only one that writes into the
  chat. How it hands work over, how long it may take, what it does with a screen and what
  opening an item means are the same for every session, and are written under
  tests/brd/tg_agent_shell/. What is here is what says Advisor rather than session: the voice
  every part of Safwa answers in.

  Background:
    Given the owner is talking to Safwa

  Scenario: AD-VOICE-001 — Every part of Safwa answers in one voice
    Given the Advisor handed a request to another part of Safwa
    Then that part is given the same block about voice, language and citations that every
      other part is given
    And so the owner's language, and the shape a named item takes, are decided once rather
      than once per part

  Scenario: AD-MEMORY-002 — The Advisor is told how to read what the retro left
    Given the memory the Advisor is given before every answer (MEM-RETRO-010)
    Then it is told that a pattern one Sprint showed is a hypothesis to check in the current
      Sprint, and one two or more showed is a pattern to plan by
    And that a pattern some Sprints saw raise the day's rating and others lower it is not a rule:
      both sides are named and asked about, and nothing is planned by it
    And that the last analysed Sprint — how it went, the experiment it set, what is worth
      knowing — is for planning and advising in the current Sprint, that nothing checked the
      experiment's result, and that none of it is a durable fact about the owner

  Scenario: AD-LOG-003 — Every saved change to what the owner keeps is written down, a deletion too
    Given a Card, a Check, a Value, a Tag, a Request or a Reminder
    When it is created, changed or deleted, by the owner on a screen or by a saved proposal
    Then one row is written: its type, its id, its title at that moment, the operation, who made
      it, the running Sprint if any, and when
    And a Check answered Passed or Missed is a row too
    When the item is deleted
    Then its rows stay, and the deletion is one more, under the title it had

  Scenario: AD-LOG-004 — Asked what was done, Safwa reads the log of changes itself
    Given the owner asks what was done on a day or over a stretch of time
    When Safwa reads the log
    Then each row says whether the item was created, updated or deleted, and on which local day;
      a move, a link, a finish or an archive reads as updated
    And an item still there is cited, and a deleted one is named by the title it had, with no link
    When a read would bring back more than 20 rows (LOG_EVENTS_SHOWN = 20)
    Then 20 come back, and Safwa is told the rest were left out, to count them by kind of change
      and kind of item, and to ask the owner which to list
