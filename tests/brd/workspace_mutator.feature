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

  Scenario: WS-CONTEXT-008 — Safwa starts from the Goals of its mode
    Given the owner has open Goals of Critical, Medium and Low Priority, at several levels
    When Safwa reads the workspace in Planning
    Then it is handed Goals to consider: every open Goal and Subgoal in the focus order (CD-ORDER-066), the first 10 (CONTEXT_LIST_LIMIT = 10), its heading saying how many of how many
    When a Sprint is running
    Then it is handed Sprint Goals instead: the open Goals and Subgoals above an Action in Sprint or Today, in the same order and limit
    And a Done or archived Goal is in neither list
    And each Goal says its Priority, stage, tree_level (CD-TREE-053), compiled Deadline when there is one, the Values it serves and the Goals it stands under
    And a list with no Goal has no heading

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

  Scenario: WS-CONTEXT-010 — Today is a ranked list of its Actions, each with its own Priority
    Given Actions in Today
    Then Today Actions lists every one of them in the focus order, one line each, under the day's planned executions and effort
    And each line says its own stored Priority, key when it is key to the running Sprint, blocked when it is blocked, its executions when they are other than one, its effort while Effort Points are on, and its appointment
    And values= names the Values it serves, its own and its Goals', highest first, as links
    And under= names every Goal it stands under directly, as links
    And a title's line breaks do not break its line
    And an empty Today says its planned executions and lists nothing

  Scenario: WS-CONTEXT-011 — In a Sprint, Safwa reads the work tactically
    Given a running Sprint
    When Safwa reads the workspace
    Then it reads Today Actions, then Sprint Actions not in Today, then Sprint Goals
    And Sprint Actions not in Today are the first 10 in the focus order, a key Action before its equals (CD-ORDER-060)
    And Backlog Actions and Goals with no work in the Sprint or Today are not listed
    And each Value is listed with its description alone

  Scenario: WS-CONTEXT-012 — In Planning, Safwa reads the work strategically
    Given no Sprint is running
    When Safwa reads the workspace
    Then each Value says how many open Goals and Actions serve it, on themselves or through a Goal above them, as goals= and actions=
    And a Value nothing open serves reads goals=0 actions=0
    And it reads Today Actions, then Goals to consider, then Backlog Actions to consider, then the Actions still in Sprint, to review
    And each of the last three is the first 10 in the focus order, which reads no stage while no Sprint runs
