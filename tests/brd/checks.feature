Feature: Checks
  A Check is a question the owner asks about how things actually went. It hangs on one Card, or on
  none at all, and it is answered Passed or Missed. Until it is answered it is Pending, and a Card
  it is on cannot be called Done.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where a Check is proposed by Safwa and answered by either of them

  Scenario: CH-WRITE-001 — A Check is an observation, not a task
    Given the owner wants to record whether something held
    Then a Check carries a title and whether it repeats, and nothing else
    And it has no effort, no stage, no priority, and never counts towards a Sprint

  Scenario: CH-WRITE-002 — Safwa writes a Check, and either of them answers it
    Given a Check that exists
    Then writing one, renaming one and linking one to a Card are proposals the owner saved
    And its own screen answers it and turns repeat on or off, and offers nothing else
    And Safwa may propose the answer too, and it reaches the owner as a screen to save
    And a Check title that is empty, or nothing but spaces, is refused

  Scenario: CH-LINK-003 — A Check belongs to one Card, or to none
    Given a Check "Posture straight?"
    When it is linked to a Card
    Then linking it to a second Card is refused
    And moving it to another Card is done by taking it off the first one
    And a Check linked to no Card is allowed
    And it carries as many Values as the owner likes

  Scenario: CH-ANSWER-004 — A Check with no answer is Pending
    Given a new Check
    Then it reads as Pending everywhere: on the Card, on its own screen, and to Safwa
    And nothing was stored to say so
    And nothing puts an answered Check back to Pending except reopening the Card it is on

  Scenario: CH-ANSWER-005 — Answering a Check again only changes the answer
    Given a Check the owner answered Passed
    When the owner opens it and answers Missed instead
    Then it is Missed, and the earlier answer is gone rather than kept beside it
    And the time the observation was made does not move
    And no new instance opens, and the Card it is on is untouched

  Scenario: CH-GATE-006 — A Card cannot be Done with an unanswered Check
    Given an Action carrying a Check that has never been answered
    When the owner finishes it as Done
    Then it is refused, and the refusal names the Check that is still unanswered
    And nothing about the Action changed
    When the owner cancels the same Action instead
    Then it is cancelled with the Check left unanswered

  Scenario: CH-GATE-007 — Finishing a Card answers its Checks at the same time
    Given an Action carrying two Pending Checks
    When the owner finishes it and answers both in the same act
    Then the Action is Done and both Checks carry their answers

  Scenario: CH-GATE-008 — A repeating Check needs one answer before its Card can close
    Given an Action carrying a repeating Check that was answered once, so the next one is Pending
    When the owner finishes the Action as Done
    Then it is allowed, and the Pending instance does not hold it
    Given instead an Action whose repeating Check has never been answered on it
    When the owner finishes it as Done
    Then it is refused, and the refusal names that Check
    And an answer given on the Action this one repeated from does not count

  Scenario: CH-REPEAT-009 — Answering a repeating Check opens the next one
    Given a repeating Check on a live Card
    When the owner answers it
    Then the answered one keeps its answer and stays where it is
    And a fresh Pending instance opens on that same Card, with the same title, still repeating
    And answering the answered one again opens no second instance
    And a repeating Check on no Card opens the next one just the same

  Scenario: CH-CLOSE-010 — Closing a Card deletes its unanswered Check
    Given an Action that does not repeat, carrying a repeating Check answered once, so one is Pending
    When the owner finishes the Action
    Then the Action closes and the Pending instance is deleted outright
    And the answered ones stay on it

  Scenario: CH-CLOSE-011 — A repeating Card gives fresh Checks to the next one
    Given a repeating Action carrying a plain Check and a repeating Check, both answered
    When the owner finishes the Action
    Then a successor Action is made, and each series lands on it as exactly one Pending instance
    And the plain Check is written fresh there too, unanswered
    And the answered instances stay with the Action that closed
    And the successor cannot be finished until each of them has been answered once, by CH-GATE-008

  Scenario: CH-REOPEN-012 — Reopening a Card brings its Checks back
    Given an Action that does not repeat, closed on a plain Check and on a repeating Check
    When the owner reopens it
    Then the plain Check is that same Check, Pending again, with its answer and its answer time wiped
    And the repeating series opens a fresh Pending instance on the Card, leaving its answers alone
    And the Action cannot be finished again until both have been answered again
    Given instead an Action that repeats
    When anything tries to reopen it
    Then it is refused by CD-STAGE-017, and no Check on it changes

  Scenario: CH-ARCHIVE-013 — An answered Check is archived two Sprints later
    Given a Check that was answered
    When two Sprints have gone by since it was answered
    Then it is archived without anyone asking, off the screens by default and still in every count
    And its Card did not take it there, and it did not wait for its Card
    And the owner may archive an answered Check by hand before then
    And a Check that is still Pending cannot be archived

  Scenario: CH-DELETE-014 — Deleting a Check deletes it
    Given a Check the owner wants gone
    When the owner or Safwa asks to delete it
    Then the Check is gone, answered or not, and it is never archived instead
    And its Card stays, and so do the Values it pointed at
