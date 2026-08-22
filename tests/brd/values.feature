Feature: Values
  A Value is something the owner cares about, written down and given a name. Putting it in focus is
  how they tell Safwa to weigh it. Cards carry a Value when they are work that serves it; Checks
  carry one when they show how well it is actually being held to.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where a Value can be written by the owner or proposed by Safwa

  Scenario: PL-VALUE-001 — A Value carries a focus, and the owner turns it on and off
    Given a Value the owner has written down
    Then it also carries a focus, which is on or off
    And the Value's screen has a button that flips it, showing which way it is now
    And /values shows at a glance which Values are in focus

  Scenario: PL-VALUE-002 — Values in focus are the ones Safwa is told to weigh
    Given "Fitness" is in focus and "Tidiness" is not
    When Safwa answers the owner
    Then Safwa has been told Fitness is in focus, and not Tidiness
    And the Value arrives as something Safwa can hand straight back to the owner as a link
    And a Value the owner archived is not mentioned at all

  Scenario: PL-VALUE-003 — A critical Card that serves a Value in focus is shown to Safwa first
    Given the owner has more critical Cards than Safwa is handed (CONTEXT_CRITICAL_CARD_LIMIT = 10)
    When they are picked
    Then a critical Card that serves a Value in focus comes before one that does not
    And a Card that only serves an archived Value does not count as serving a focus
    And among the rest, a Card with a Hard Time comes first, and then the older one

  Scenario: PL-VALUE-004 — One Value is carried by many things, which is what it is for
    Given a Value named "Health"
    When the owner puts it on a morning run, a dentist appointment and a Goal
    Then all three carry Health, and Health is carried by all three
    And taking Health off one of them leaves it on the other two
    And the Value's screen says how many Cards and how many Checks carry it right now

  Scenario: PL-VALUE-005 — A Value's name is taken whatever the capitals, and is never blank
    Given a Value named "Fitness"
    When a second Value is written as "FITNESS"
    Then it is refused, because that name is taken
    And renaming another Value to "fitness" is refused the same way
    And a name that is empty, or only spaces, is refused

  Scenario: PL-VALUE-006 — Writing down a Value the owner archived brings that one back
    Given a Value named "Fitness" was archived, and it had a description
    When a Value is written down under that name again, in any capitals
    Then it is the archived one that comes back, still the same Value
    And it keeps the description it had, unless a new one was given
    And a description nobody typed is not a new one
    And it comes back with its focus off, and on nothing, because archiving took it off everything
    And there is still only one Fitness

  Scenario: PL-VALUE-007 — Archiving a Value takes it off everything, and the things it was on stay
    Given a Value in focus, carried by Cards and by Checks
    When the owner archives it
    Then they are asked first, and told how many Cards and Checks it is about to come off
    And it comes off all of them at the moment it is archived
    And those Cards and Checks are otherwise untouched: none of them is deleted, moved or changed
    And the Value's focus is off
    And a Value that is already archived cannot be archived again

  Scenario: PL-VALUE-008 — A Value is archived, never deleted
    Given something wants a Value gone
    Then archiving is the only way it goes
    And a request to delete one is refused before anything is written
    And the owner's screen offers Archive and no delete either

  Scenario: PL-VALUE-009 — Nothing can be linked to a Value that is archived
    Given a Value the owner archived
    When something tries to put it on a Card or a Check, by its name or by its number
    Then it is refused
    And a name that belongs to no Value is refused too, and says which name it was
    And nothing is half-done: if one name in a link cannot be found, none of them are linked

  Scenario: PL-VALUE-010 — A Check can carry a Value, because a Check shows how well it is held to
    Given a Value named "Health" and a Check "Did I sleep seven hours?"
    When the owner puts Health on that Check
    Then the Check carries Health, and Health is carried by that Check
    And the link is put on and taken off from the Check
    And a Check's Values and a Check's Cards have nothing to do with each other: putting a Value on
      a Check changes nothing about the Cards that Check belongs to, and their Values are not its own

  Scenario: PL-VALUE-011 — Archiving a Check keeps its Values; deleting one takes them away
    Given a Check carrying a Value
    When that Check is archived
    Then it still carries the Value, so it has it again if it comes back
    When that Check is deleted instead
    Then the link goes with it, and the Value is not left pointing at something that is gone

  Scenario: PL-VALUE-012 — An answered repeat hands its Values to the copy that takes its place
    Given a repeatable Check carrying a Value
    When it is answered, Passed or Missed
    Then the fresh copy that takes its place carries that Value
    And the answered one no longer does
    And so a Value is carried by the Check the owner is still answering, and never by a pile of
      finished ones
    But a Check that does not repeat keeps its Values when it is answered, because it is the record
      of that one observation

  Scenario: PL-VALUE-013 — Safwa can see which Values a Check is about
    Given Checks carry Values
    When Safwa looks at a Check
    Then it sees the Values on it, by name
    And that is how it knows which Value the owner is working on through that Check

  Scenario: PL-VALUE-014 — Safwa can start from a Value and find what is behind it
    Given a Value named "Health"
    When Safwa is asked how Health is going
    Then starting from the name Health it can find the Checks that carry it, and the Cards that
      carry it
    And the Checks are the evidence: they say how well Health is actually being held to
    And the Cards are the work: they say what is being done about it
    And an archived Card or Check is in neither answer
