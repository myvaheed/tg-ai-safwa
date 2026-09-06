Feature: Retro
  The retrospective itself is not written yet. What exists is the screen a finished Sprint
  leaves behind, and the rule about who may put it up.

  Background:
    Given a Sprint that has ended, and the message Safwa sent about it (PL-END-015)

  Scenario: RT-OPEN-001 — A Sprint that ended keeps a screen of its own
    Given the owner taps the "Sprint retro" link at the end of that message
    Then a screen names that Sprint by its number, the dates it ran, and the Success criteria
      it was given
    And it says there is nothing there yet
    And it arrives as its own message, offering the way back to the menu and nothing else

  Scenario: RT-OPEN-002 — The retro is the owner's to open, never Safwa's
    Given Safwa is answering a question about a Sprint that has ended
    Then it may name the retro as a link the owner can tap
    And it has no way to put that screen up itself, which a Card, a Check, a Value, a Tag, a
      Request and a day of the Diary all have
