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
