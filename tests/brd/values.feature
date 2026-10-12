Feature: Values
  A Value is something the owner cares about, written down and given a name. Cards carry a Value
  when they are work that serves it; Checks carry one when they show how well it is actually being
  held to. A Value is in focus while today's work carries it.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where a Value can be written by the owner or proposed by Safwa

  Scenario: VL-DESC-020 — A Value's description holds at most 300 characters
    Given a Value
    When its description is written with 301 characters, on its screen or by Safwa (DESCRIPTION_MAX_CHARS = 300)
    Then it is refused with the limit and the length it has, and the Value keeps the description it had
    And the screen's editor stays open with that message, and its instruction names the limit
    And Safwa's value tool says the limit in the description field
    And a call of Safwa's over it comes back to Safwa refused, with no screen shown

  Scenario: VL-LINK-018 — A link means the item supports the Value
    Given Advisor, Mutator and Onboarding explain Value links
    Then a Goal, Action or Check is linked for supporting the Value, not for needing it
    And earning $4k supports Freedom through more choices
    And earning $4k supports Health only when the money maintains health
    And needing Health or Perseverance to earn it is not a supporting link
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

  Scenario: VL-FOCUS-001 — A Value is in focus while a Card in Today carries it
    Given "Health" is on an Action in Today
    And "Family" is on a Goal whose Subgoal holds an Action in Today
    And "Craft" is on an Action in Sprint, on a Goal with nothing in Today, and on a Check
    Then Health and Family are in focus, and Craft is not
    When the Action carrying Health leaves Today, by moving or by being finished
    Then Health is out of focus
    And neither the Value's screen nor /values offers a way to set it by hand

  Scenario: VL-FOCUS-002 — Safwa is handed every Value
    Given "Fitness" and "Tidiness", only Fitness in focus
    When Safwa answers the owner
    Then Safwa has been handed both, by name and in alphabetical order
    And each comes with its description on its own line
    And each arrives as something Safwa can hand straight back to the owner as a link

  Scenario: VL-LINK-004 — One Value is carried by many things, which is what it is for
    Given a Value named "Health"
    When the owner puts it on a morning run, a dentist appointment and a Goal
    Then all three carry Health, and Health is carried by all three
    And taking Health off one of them leaves it on the other two
    And the Value's screen says how many Cards and how many Checks carry it right now

  Scenario: VL-LINK-017 — A Card's screen names the Values on it, and a Goal with none says so
    Given a root Goal carrying no Value, and an Action carrying "Health"
    When each of them is opened
    Then every Value on the Card is a button that opens that Value
    And a root Goal carries its Values in the compact view, where every other Card shows them in
      full editing, because a Goal is where a Value is what says why the work is there
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
