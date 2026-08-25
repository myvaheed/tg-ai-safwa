Feature: Values
  A Value is something the owner cares about, written down and given a name. Putting it in focus is
  how they tell Safwa to weigh it. Cards carry a Value when they are work that serves it; Checks
  carry one when they show how well it is actually being held to.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where a Value can be written by the owner or proposed by Safwa

  Scenario: VL-FOCUS-001 — A Value carries a focus, and the owner turns it on and off
    Given a Value the owner has written down
    Then it also carries a focus, which is on or off
    And the Value's screen has a button that flips it, showing which way it is now
    And /values shows at a glance which Values are in focus

  Scenario: VL-FOCUS-002 — Values in focus are the ones Safwa is told to weigh
    Given "Fitness" is in focus and "Tidiness" is not
    When Safwa answers the owner
    Then Safwa has been told Fitness is in focus, and not Tidiness
    And the Value arrives as something Safwa can hand straight back to the owner as a link

  Scenario: VL-READ-003 — A critical Card that serves a Value in focus is shown to Safwa first
    Given the owner has more critical Cards than Safwa is handed (CONTEXT_CRITICAL_CARD_LIMIT = 10)
    When they are picked
    Then a critical Card that serves a Value in focus comes before one that does not
    And among the rest, a Card with a Hard Time comes first, and then the older one

  Scenario: VL-LINK-004 — One Value is carried by many things, which is what it is for
    Given a Value named "Health"
    When the owner puts it on a morning run, a dentist appointment and a Goal
    Then all three carry Health, and Health is carried by all three
    And taking Health off one of them leaves it on the other two
    And the Value's screen says how many Cards and how many Checks carry it right now

  Scenario: VL-NAME-005 — A Value's name is taken whatever the capitals, and is never blank
    Given a Value named "Fitness"
    When a second Value is written as "FITNESS"
    Then it is refused, because that name is taken
    And renaming another Value to "fitness" is refused the same way
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
    Given a repeatable Check carrying a Value
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
