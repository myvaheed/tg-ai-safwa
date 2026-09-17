Feature: Retro
  The retrospective is the screen a finished Sprint leaves behind: what it added up to, read
  off its own record. What Safwa makes of those numbers is not written yet.

  Background:
    Given a Sprint that has ended, and the message Safwa sent about it (PL-END-015)

  Scenario: RT-OPEN-001 — A Sprint that ended keeps a screen of its own
    Given the owner taps the "Sprint retro" link at the end of that message
    Then a screen names that Sprint by its number, the dates it ran, and the Success criteria
      it was given
    And below them, what the Sprint added up to (RT-STATS-003)
    And it arrives as its own message, offering the way back to the menu and nothing else

  Scenario: RT-OPEN-002 — The retro opens the way every other cited item does
    Given Safwa is answering a question about a Sprint that has ended
    Then it may name the retro as a link the owner can tap
    And asked to see that retro, it puts the screen up itself, exactly as it does for a Card,
      a Check, a Value, a Tag, a Request and a day of the Diary

  Scenario: RT-STATS-003 — The retro adds the Sprint up from its own record
    Given the Sprint's commitments, the Actions they name, and the Checks tied to a Value
    Then the screen shows the effort taken into the Sprint — the initial plan and what was added along the way together — and the effort finished, with the finished share in whole percent
    And of the effort taken: the initial plan, what was added, and what was taken back out
    And how many Actions finished, how many remain in the Sprint, and how many of those are blocked; an Action taken out is in none of the three
    And for each Check series tied to a Value, answered while the Sprint ran: Passed and Missed counts, by the Check's title and its Values
    And a Check tied to no Value is not on the screen, and neither is a series with no answer in the Sprint
    And every number is read off the record: the model writes none of it
