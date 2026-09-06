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
