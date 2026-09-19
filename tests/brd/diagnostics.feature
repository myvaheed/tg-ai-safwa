Feature: Diagnostics
  One command, no item and no screen to come back to. It answers the question the owner asks
  when something feels wrong: is Safwa where I think it is.

  Background:
    Given a running Safwa the owner can ask about itself

  Scenario: DG-STATUS-001 — The status says which mode the workspace is in and how far it has moved
    Given the owner asks for the status
    Then Safwa answers with the mode the workspace is in and the revision it has reached
    And it is one message the owner reads and leaves, not a screen with anything to press
