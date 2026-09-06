Feature: The workspace
  The workspace is what the owner keeps: their Cards, Checks, Values, Tags, Requests and
  Reminders. It is not an item and it has no screen. What is here is the one part of Safwa
  that proposes every change to it, and what that part is given before it proposes one.

  Background:
    Given the owner has asked Safwa for a change to something they keep

  Scenario: WS-SCOPE-001 — What the owner keeps is changed by one part of Safwa
    Given a request that would create, change, archive or delete a Card, a Check, a Value, a
      Tag, a Request or a Reminder
    Then all of it is handed to the same part, however many kinds it touches
    And the Diary is not part of the workspace: a day is written by the part that owns days
    And every way Safwa has of changing anything belongs to one of those two, so a way that
      reaches neither reaches nothing

  Scenario: WS-JUDGE-002 — It is told what the workspace is before it changes it
    Given a Sprint is running with Success criteria, and the owner has Values in focus
    When that part starts work
    Then it is handed the state of the workspace before its first step
    And it weighs what it is about to propose against what it was handed, rather than against
      the request alone

  Scenario: WS-REMOVE-003 — Deleting is one way, and archiving is not a quieter kind of it
    Given anything the owner keeps
    Then Safwa has exactly one way to take it away, whatever kind it is
    And that way also archives, but only a Card or a Check, which are the two the owner makes
      many of and finishes
    When Safwa asks to archive anything else
    Then it is refused, and never turned into a deletion instead
