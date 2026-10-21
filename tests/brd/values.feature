Feature: Values
  A Value is something the owner cares about, written down and given a name. Cards carry a Value
  when they are work that serves it; Checks carry one when they show how well it is actually being
  held to. A Value is in focus while today's work carries it.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where a Value can be written by the owner or proposed by Safwa

  Scenario: VL-ORDER-026 — Every Value has a unique place in the Values list
    Given N Values
    Then their places are the integers 1 through N, and 1 is highest
    When a Value is put at another place
    Then the Values between its old and new places shift by one, and the others keep their places
    And a place outside 1 through N or not a whole number is refused
    When a Value is created without a place
    Then it is added at the end
    When a Value is created at a given place
    Then the Values at that place and below shift down
    When a Value is deleted
    Then the Values below it move up to close the gap
    And every change is saved together or rolled back together
    And these places order the Values list and the Values handed to Safwa

  Scenario: VL-ORDER-027 — The owner prioritizes Values by selecting one and moving it
    Given the Values list in order, five to a page
    When the owner presses "↕️ Prioritize"
    Then tapping a Value selects it with the same tick as other selectors
    And tapping it again clears the selection; tapping another selects that one alone
    And with no selection the only controls are previous page, next page and Back
    And with a selection there are also up and down, moving it by one place immediately
    And turning a page keeps the selection, and moving follows the Value onto its new page
    And the arrows remain at the first and last places and pages and do nothing past an edge
    And Back leaves prioritizing and keeps the saved order

  Scenario: VL-ORDER-028 — Safwa sets one Value's place and the others shift automatically
    Given a Value called "Family" at place 5
    When the owner asks to put Family second
    Then Safwa proposes updating Family with order 2 alone
    And the review shows its old and new places
    And saving puts Family second and shifts the former places 2 through 4 down by one
    And an invalid place is refused before review
    And a proposal is refused if the order changed after it was prepared

  Scenario: VL-ORDER-029 — Every live list of Values uses their priority order
    Given Values whose priorities differ from both their names and their IDs
    Then their selectors, linked names on Cards and Checks, drafts, proposal screens and receipts,
      Home, Compass, Life and Safwa context list them highest first
    And SQL lists default to this order, including linked Value names, before any limit
    But an explicit SQL order takes precedence
    When a Value's priority changes
    Then the next rendering or read uses the new order
    And closed Sprint retros retain the order recorded when they closed

  Scenario: VL-DESC-020 — A Value's description holds at most 400 characters
    Given a Value
    When its description is written with 401 characters, on its screen or by Safwa (DESCRIPTION_MAX_CHARS = 400)
    Then it is refused with the limit and the length it has, and the Value keeps the description it had
    And the screen's editor stays open with that message, and its instruction names the limit
    And Safwa's value tool says the limit in the description field
    And a call of Safwa's over it comes back to Safwa refused, with no screen shown

  Scenario: VL-LINK-018 — A link means the item makes more of the Value
    Given Advisor, Mutator, Onboarding and the link check explain Value links
    Then a Goal, Action or Check is linked when achieving it, doing it or its holding makes more of the Value in the owner's life, as the Value's description says
    And Onboarding and the link check add that a link is never made for needing the Value
    And earning $4k serves Freedom through more choices
    And earning $4k serves Health only when the money maintains health
    And needing Health or Perseverance to earn it is not a supporting link
    And earning $20k does not serve Punctuality kept for the owner's self-respect, while a Check "On time?" does
    And the owner's own clear supporting explanation can establish a different connection
    And a link is unclear when supporting and reversed meanings are both plausible and no context resolves them

  Scenario: VL-LINK-019 — A saved direct link gets one small check before any question
    Given a Value is newly linked to a Goal, Action or Check, manually or by a saved proposal
    When the change commits
    Then one mini session reads only that pair's title, Note and Value description
    And an obvious supporting link produces no question or change
    And an unclear or reversed link asks to clarify Note
    And the Advisor may suggest a possible supporting explanation and ask whether it matches the owner's meaning
    And a suggestion is not treated as the owner's reason, and nothing changes before the owner answers
    And a rollback, unlink or copied repeat starts no review
    And one hook does the check and keeps an unclear pair, and one request asks about every pair still linked
    And a link removed or changed during review produces no queued request
    And the queued request tells the Advisor to read current data before asking
    And the request waits like any hook request while access is locked
    And switching Value link clarification off in Profile → Hooks stops the check and drops what is pending

  Scenario: VL-UNLINK-021 — A Note that still mentions a removed Value is proposed without it
    Given a Value is taken off a Goal, Action or Check, or deleted from all of them, by hand or by a saved proposal
    When the change commits
    Then each item with a Note gets one mini session that reads its title and Note, and the Value's name and description
    And an item with an empty Note, archived, deleted or carrying the Value again gets no mini session
    And a Note that does not mention the Value produces nothing
    And a Note changed during the check produces nothing for that item
    And the items one change took the Value off that still mention it make one pending request
    And the request tells the Advisor to read the current Note and route to workspace_mutator to propose each Note without the part about that Value
    And the proposal keeps the rest of the Note, and the Note changes only on Save
    And a rollback starts no check
    And the request waits like any hook request while access is locked
    And switching Value unlink cleanup off in Profile → Hooks stops the check and drops what is pending

  Scenario: VL-CLARIFY-022 — A new Value gets one small check before any question
    Given a Value is created, by hand or by a saved proposal
    When the change commits
    Then one mini session reads only its name and description
    And it answers clear for a direction the owner lives by whose description says what they do for it on an ordinary day
    And it answers unclear, with what is unclear and one advice, for a result, one practice, a feeling or a rule in the name, a turn away from something, a means whose description says only what it buys, or a description that does not say what the owner does
    And a name about others, such as their smile, is clear when the description says what the owner does
    And the advice may hold a short exercise, such as asking "what for?" again or how the Value looks on an ordinary day
    And it does not judge whether the Value is right for the owner, or how vivid its words are
    And a clear Value produces no question or change
    And a rename, a new description or a rollback starts no review
    And a Value renamed, described anew or deleted during review produces no queued request
    And one request carries every Value still there, each with its advice, and tells the Advisor to read current data before asking
    And the Advisor says it looked at the new Value again and it could be improved, says what could be clearer, and asks one question following its advice
    And it offers each suggestion as a possibility and to update the Value, and nothing changes before the owner answers
    And the answer updates the Value, or proposes a Goal or a repeating Action instead
    And the request waits like any hook request while access is locked
    And switching Value clarification off in Profile → Hooks stops the check and drops what is pending

  Scenario: VL-MEANING-023 — Safwa says what a Value is before proposing one
    Given Advisor and Mutator explain Values
    Then a Value is a direction the owner lives by, with no deadline, and never Done
    And a Value is not a result, a practice, a feeling, a means or a rule
    And a new Value has a short, vivid name and a description of two or three vivid sentences on why it matters and what the owner does for it on an ordinary day
    And both are built from the owner's words: Safwa asks why it matters and what they do before proposing it, and never invents either

  Scenario: VL-DESC-024 — A Value is not created without a description
    Given the owner writes a new Value on its screen, or Safwa proposes one
    When it has a name and no description
    Then its screen offers no way to create it yet, says it needs a name and a description, and the description editor asks why the Value matters and what the owner does for it
    And Safwa's value tool refuses a new Value without a description, with no screen shown
    And a description cannot be emptied later: the Value keeps the one it had

  Scenario: VL-FOCUS-001 — A Value is in focus while today's work or scheduled observations carry it
    Given "Health" is on an Action in Today
    And "Family" is on a Goal whose Subgoal holds an Action in Today
    And "Craft" is on an Action in Sprint, on a Goal with nothing in Today, and on a Check
    Then Health and Family are in focus, and Craft is not
    When the Action carrying Health leaves Today, by moving or by being finished
    Then Health is out of focus
    And neither the Value's screen nor /values offers a way to set it by hand
    Given a Pending Check carries a Value and has an appointment or daily quota with answers left today
    Then that Value is in focus even without a Card in Today
    But a Check without a Schedule, one answered or archived, one scheduled for another day, and a weekly or monthly quota alone do not put a Value in focus

  Scenario: VL-FOCUS-002 — Safwa is handed every Value
    Given "Fitness" and "Tidiness", only Fitness in focus
    When Safwa answers the owner
    Then Safwa has been handed both, by name and in their order, highest first
    And each comes with its description on its own line
    And each arrives as something Safwa can hand straight back to the owner as a link

  Scenario: VL-LINK-004 — One Value is carried by many things, which is what it is for
    Given a Value named "Health"
    When the owner puts it on a morning run, a dentist appointment and a Goal
    Then all three carry Health, and Health is carried by all three
    And taking Health off one of them leaves it on the other two
    And the Value's screen sums up the Cards carrying it right now, by VL-LINK-025, and links
      to the Checks carrying it with their answer totals, by VL-LINK-030

  Scenario: VL-LINK-017 — A Card's screen names the Values on it, and a Goal with none says so
    Given a root Goal carrying no Value, and an Action carrying "Health"
    When each of them is opened
    Then every Value on the Card is a link by its name alone that opens that Value, by CD-LINK-055
    And every Card carries its attached Values in both the compact view and full editing
    And an Action or Subgoal with no Values omits the line in the compact view
    And the root Goal with no Value says so with a warning rather than a dash
    And nothing is refused over it: the link stays optional on every kind

  Scenario: VL-NAME-005 — A Value's name is taken whatever the capitals, and is never blank
    Given a Value named "Fitness"
    When a second Value is written as "FITNESS"
    Then it is refused, because that name is taken
    And renaming another Value to "fitness" is refused the same way
    And Unicode case is treated the same: "Здоровье" takes "ЗДОРОВЬЕ", and "Straße" takes "STRASSE"
    And a name that is empty, or only spaces, is refused

  Scenario: VL-CHECK-010 — A Check can carry a Value, because a Check shows how well it is held to
    Given a Value named "Health" and a Check "Did I sleep seven hours?"
    When the owner puts Health on that Check
    Then the Check carries Health, and Health is carried by that Check
    And the link is put on and taken off from the Check
    And a Check's Values and a Check's Cards have nothing to do with each other: putting a Value on
      a Check changes nothing about the Cards that Check belongs to, and their Values are not its own

  Scenario: VL-CHECK-011 — Archiving a Check keeps its Values; deleting one takes them away
    Given a Check carrying a Value
    When that Check is archived
    Then it still carries the Value, so it has it again if it comes back
    When that Check is deleted instead
    Then the link goes with it, and the Value is not left pointing at something that is gone

  Scenario: VL-CHECK-012 — An answered repeat hands its Values to the copy that takes its place
    Given an independent scheduled Check carrying a Value
    When it is answered, Passed or Missed
    Then the fresh copy that takes its place carries that Value
    And the answered one no longer does
    And so a Value is carried by the Check the owner is still answering, and never by a pile of
      finished ones
    But a Check that does not repeat keeps its Values when it is answered, because it is the record
      of that one observation

  Scenario: VL-READ-013 — Safwa can see which Values a Check is about
    Given Checks carry Values
    When Safwa looks at a Check
    Then it sees the Values on it, by name
    And that is how it knows which Value the owner is working on through that Check

  Scenario: VL-READ-014 — Safwa can start from a Value and find what is behind it
    Given a Value named "Health"
    When Safwa is asked how Health is going
    Then starting from the name Health it can find the Checks that carry it, and the Cards that
      carry it
    And the Checks are the evidence: they say how well Health is actually being held to
    And the Cards are the work: they say what is being done about it
    And an archived Card or Check is in both answers, marked "[📦]"

  Scenario: VL-DELETE-015 — A Value is deleted, not archived
    Given a Value carried by Cards and by Checks
    When the owner or Safwa asks for it to be removed
    Then it is deleted, and every Card and Check that carried it loses that link and nothing else
    And no screen offers to archive a Value, and the remove tool refuses to
    And its name is free from that moment, and a new Value taking it is a new Value

  Scenario: VL-CHECK-016 — A repeating Card hands the Check's Values to the next cycle
    Given a repeating Action with a Check that carries a Value
    When the owner finishes the Action and the next cycle's Card appears
    Then the Check on the new Card carries that Value
    And the answered one no longer does
    And so the Value is measured by every cycle, not by one finished Check on an archived Card

  Scenario: VL-LINK-025 — A Value's Linked Cards read as a Goal's children do, and open as a list
    Given a Value carried by a Goal and by an Action
    When its screen is opened
    Then "Linked Cards:" counts those Cards as CD-LINK-055 counts a Goal's children: "🎯1 · ⭐️1",
      with Done of all their Effort Points while Effort Points are on
    And it is a link that opens those Cards as a list, by CD-ORDER-054
    And ↩️ Back on that list returns to the Value
    And a Value no Card carries reads "Linked Cards: —", with no link

  Scenario: VL-LINK-030 — Linked Checks open directly with their answer totals
    Given a Value or Card carrying Checks, including answered and archived ones
    When its screen is opened
    Then "Linked Checks:" shows up to 5 attached Checks (LINKED_CHECKS_LIMIT = 5), in creation order
    And its heading is italic, and each Check starts with a bullet
    And each reads "Slept enough? ✅8 · ❌2", with Passed and Missed counts for that Check's series,
      including archived answers and keeping its archive or repeat marks
    And its title is a link opening that Check directly, and ↩️ Back returns to the Value or Card
    And a repeating Check appears once, using the instance currently attached
    And both the compact Card and full editing show these links
    And more than 5 Checks add a final bullet "Others(3)", counting the remaining Checks without statuses
    And Others opens all Checks attached to that Card or Value, including the five already shown,
      in pages of 25 (CHECK_LIST_LIMIT = 25), with Refresh and Back
    And at most 5 Checks offer no Others link
    And a Value no Check carries shows a dash under "Linked Checks:", with no link

  Scenario: VL-EDIT-030 — A Value proposal is edited with the Value's own controls
    Given Safwa proposes to create or change a Value
    When the owner presses "✏️ Edit" on its review screen
    Then each field the proposal sets has the button the Value's own screen gives it: "✏️ Name"
      and "📝 Description"
    And each asks for the words the Value's own screen asks for, and refuses what it refuses
    And a Value's place has no button
