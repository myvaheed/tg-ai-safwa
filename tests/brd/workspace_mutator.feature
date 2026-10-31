Feature: The workspace
  The workspace is what the owner keeps: their Cards, Checks, Values, Tags and Requests. It is
  not an item and it has no screen. What is here is the one part of Safwa that proposes every
  change to it, and what that part is given before it proposes one.

  Background:
    Given the owner has asked Safwa for a change to something they keep

  Scenario: WS-SCOPE-001 — What the owner keeps is changed by one part of Safwa
    Given a request that would create, change or delete a Card, a Check, a Value, a
      Tag or a Request
    Or a request that would archive a Card or Check
    Then all of it is handed to the same part, however many kinds it touches
    And the Diary is not part of the workspace: a day is written by the part that owns days
    And a Reminder, and the Remind on an item already made, are set by their own part, by
      RM-AGENT-041; a new item is proposed with its Remind, by SCH-REMIND-029
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
    And a Reminder is not kept in the workspace: its own part deletes it, by RM-AGENT-041
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
    And each Goal says its Priority when it is not Medium, its stage, tree_level (CD-TREE-053), its compiled Deadline when there is one, the Values it serves and the Goals it stands under
    And a Deadline is counted from today as an appointment is (WS-CONTEXT-010), with its time only when the Deadline names one
    And a list with no Goal has no heading

  Scenario: WS-CAPTURE-015 — A captured record waits for a decision, not for filled fields
    Given the owner asks to record a note, idea or draft
    Then it is proposed as a Thought, without asking for extra detail
    And editing its content keeps it a Thought
    And saving it changes no other Card
    And its type remains Thought until the owner deletes it
    And creating other item types follows the Profile setting by PS-THOUGHT-026

  Scenario: WS-FIND-009 — The workspace finds the item the owner describes
    Given the owner describes an item in other words than its title
    When they ask for a change to it
    Then the workspace searches for those words and proposes the change on the closest item
    And when no item is close, it changes nothing and says it could not find the item

  Scenario: WS-CONTEXT-010 — Today is a ranked list of its Actions, each with its own Priority
    Given Actions in Today
    Then Today Actions lists every one of them in the focus order, one line each
    And its heading says how many, and the planned executions left today and, while Effort Points are on, the effort left
    And each line says its own stored Priority when it is not Medium, key when it is key to the running Sprint, blocked when it is blocked, its executions when they are other than one, its effort while Effort Points are on, and its appointment
    And an appointment says its date in words with its year, its time unless it takes the whole day,
      and how far it is from today: today, yesterday, tomorrow, in how many days or how many days
      ago (WS-CONTEXT-014)
    And a passed one also says overdue
    And an appointment for the whole day passes when that day ends
    And values= names the Values it serves, its own and its Goals', highest first, by name
    And under= names every Goal it stands under directly, as links
    And a title's line breaks do not break its line
    And an empty Today says Today Actions: none

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
    Then each Value says how many open Goals, Actions and Checks serve it, as goals=, actions= and checks=
    And a Goal or an Action serves it on itself or through a Goal above it, and an unanswered Check on itself
    And a count of zero is left out, so a Value nothing open serves carries no count
    And it reads Today Actions, then Goals to consider, then Backlog Actions to consider, then the Actions still in Sprint, to review
    And each of the last three is the first 10 in the focus order, which reads no stage while no Sprint runs

  Scenario: WS-CONTEXT-013 — The part that changes the workspace is handed the open items the owner is talking about
    Given open Goals, Actions and Checks
    Then at every step, after the conversation, the workspace mutator reads up to 10 of them close
      to its end by words or by meaning, the closest first (CLOSE_ITEMS_MAX = 10)
    And each is its link, as the state writes one (WS-CONTEXT-006), then Goal, Action or Check, an
      Action's or a Check's Schedule, a Goal's deadline, and the Card a Check hangs on
    And a Done Card, an answered Check, an archived item, a Value and a Tag are never among them
    And when none is close, nothing is added

  Scenario: WS-CONTEXT-014 — Safwa is told a date in words, and how far it is from today
    Given the owner's today is Tuesday, 27 October 2026
    Then the Advisor, the workspace mutator, the Diary, the Sprint and the Retro subagents and the
      Home motivation are each told today in one line: "Today is Tuesday, 27 October 2026
      (2026-10-27), 14:05, Europe/Istanbul."
    And every other date they are told says its month by name and its year, then how far it is
      from today: "21 October 2026 (6 days ago)", "(yesterday)", "(today)", "(tomorrow)",
      "(in 6 days)"
    And so does what a hook asks Safwa to say about a Sprint, a Check's Missed run or an
      Action's appointment
    But a screen, a Schedule's description, what Safwa remembers, the hour marks of the
      conversation, the Retro analysis and the sessions that read a time from the owner's words
      keep their own dates
