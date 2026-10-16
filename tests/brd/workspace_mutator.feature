Feature: The workspace
  The workspace is what the owner keeps: their Cards, Checks, Values, Tags, Requests and
  Reminders. It is not an item and it has no screen. What is here is the one part of Safwa
  that proposes every change to it, and what that part is given before it proposes one.

  Background:
    Given the owner has asked Safwa for a change to something they keep

  Scenario: WS-SCOPE-001 — What the owner keeps is changed by one part of Safwa
    Given a request that would create, change or delete a Card, a Check, a Value, a
      Tag, a Request or a Reminder
    Or a request that would archive a Card or Check
    Then all of it is handed to the same part, however many kinds it touches
    And the Diary is not part of the workspace: a day is written by the part that owns days
    And every way Safwa has of changing anything belongs to the part that owns what it changes,
      so a way that reaches no part reaches nothing

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
    And each active Value and each available Tag is on its own line with its description
    And a description arrives as one line of at most 120 characters (CONTEXT_DESCRIPTION_MAX_CHARS = 120)
    And an item with no description is its link alone, and a list with nothing in it has no heading
    And the Advisor and the part that changes the workspace receive those descriptions

  Scenario: WS-CONTEXT-007 — Safwa is told what time it is where the owner is, and told it last
    Given the owner's timezone
    Then it is told the time there, after everything else it was handed
    And that line is the only part of what it reads before the conversation that differs between
      two turns a minute apart, which is what keeps the rest of it cheap

  Scenario: WS-CONTEXT-008 — Safwa starts from the Goals that need attention first
    Given the owner has open Goals of Critical, Medium and Low Priority
    When Safwa reads the workspace
    Then it is handed the first 10 Priority Goals (CONTEXT_PRIORITY_GOAL_LIMIT = 10)
    And that list includes only open root Goals, not Subgoals, Actions or archived Goals
    And those Goals use the same Deadline groups, height, own Priority and Value score as
      every Goal list (CD-ORDER-061)
    And child Actions neither raise a Goal's Priority nor supply its Value score
    And Goals created at the same instant keep their creation order
    And each Goal says its Priority, stage, tree_level (CD-TREE-053) and compiled Deadline when there is one
    And when there are no open Goals, there is no empty Priority Goals heading

  Scenario: WS-CAPTURE-008 — A captured record waits for a decision, not for filled fields
    Given the owner asks to record a note, idea or draft
    Then it can be captured as an Action with Tag "Inbox", without extra detail
    When the Card's fields are edited
    Then the Tag stays attached
    When the owner explicitly resolves the input or asks to remove the Tag
    Then it is taken off that Card, without requiring any set of filled fields

  Scenario: WS-FIND-009 — The workspace finds the item the owner describes
    Given the owner describes an item in other words than its title
    When they ask for a change to it
    Then the workspace searches for those words and proposes the change on the closest item
    And when no item is close, it changes nothing and says it could not find the item

  Scenario: WS-CONTEXT-010 — Today is a hierarchy table with each Action's own Priority
    Given Actions in Today and all their ancestor Goals
    Then Today Actions is a table with two columns, Card and Priority
    And its branches follow Home's order, each placed by its first Action (PL-KEY-025)
    And the first column preserves hierarchy indentation and item links
    And each Card has one row, under its last shown parent, with the other parents named as links
    And the Priority column is empty for Goals and Subgoals
    And an Action's Priority is its own stored priority, never one taken from a parent or sorting group
    And execution quantities, effort and appointment details stay on Action rows
    And the summary counts distinct Actions
    And title line breaks and pipes cannot add rows or columns to the table
    And Priority Goals remains a separate, independently selected list (WS-CONTEXT-008)
    And an empty Today has no empty table
