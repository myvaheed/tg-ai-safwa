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

  Scenario: WS-CONTEXT-004 — Safwa is handed the workspace every turn, and a part it hands work to only when that part asks
    Given any turn, whatever the owner asked about
    Then the part that answers in the chat is handed the state of the workspace before the
      conversation, so an ordinary question is answered without looking anything up
    And a part the turn is handed on to is given that same state only when it asks for it: the
      one that changes the workspace asks, and the one that writes days does not

  Scenario: WS-CONTEXT-005 — The first thing said about the workspace is which of its two modes it is in
    Given the workspace is in Planning, or a Sprint is running
    Then the first line of that state says which of the two it is
    And the two things the owner wrote about themselves come next, before anything they keep

  Scenario: WS-CONTEXT-006 — Everything named in that state is already written as a link
    Given the state names a Value, a Tag or a Card
    Then each of them is written the way Safwa writes a link into its own reply
    And so pointing the owner at one is quoting what Safwa was handed, never building a link out
      of an id it read somewhere else

  Scenario: WS-CONTEXT-007 — Safwa is told what time it is where the owner is, and told it last
    Given the owner's timezone
    Then it is told the time there, after everything else it was handed
    And that line is the only part of what it reads before the conversation that differs between
      two turns a minute apart, which is what keeps the rest of it cheap
