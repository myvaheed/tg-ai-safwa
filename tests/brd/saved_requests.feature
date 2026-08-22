Feature: Saved Requests
  A Request is a saved question about the owner's Cards: a name, a description, and a query Safwa
  wrote. The owner never writes one — Safwa proposes it, the owner saves it, and from then on it is
  a button that answers the same question again. What comes back is always a list on a screen and
  never something Safwa reads back, so the query has to be valid and nothing else.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where the only way a Request gets written is the owner approving one

  Scenario: SR-WRITE-001 — A Request exists because Safwa proposed one and the owner saved it
    Given the owner is looking at their saved Requests
    When they look for a way to write one themselves
    Then there is none: the screens list them, open them and run them, and nothing else
    And the only way one comes into being is the owner approving a proposed Request
    And the part of Safwa that keeps the board is what proposes one; the part that talks to the
      owner never does

  Scenario: SR-WRITE-002 — A Request's name is taken whatever the capitals, and is never blank
    Given a Request named "All goals"
    When a second one is saved as "ALL GOALS"
    Then it is refused, because that name is taken
    And renaming another Request onto that name is refused the same way
    And a name that is empty, or only spaces, is refused

  Scenario: SR-WRITE-003 — Saving under an archived Request's name brings that one back
    Given a Request named "All goals" was archived, and it had a description
    When a Request is saved under that name again
    Then it is the archived one that comes back, still the same Request
    And it asks the new question, not the old one
    And it keeps the description it had, because none was given
    And there is still only one Request with that name

  Scenario: SR-SQL-004 — A Request's query only reads, and it has to come back with Cards
    Given a Request is being saved
    When its query is checked
    Then it must be a single SELECT, or WITH … SELECT, over the views Safwa is allowed to read
    And it must ask about Cards
    And it must come back with a column named id, so the answer is Cards and not numbers
    And anything else is refused, and no Request is written

  Scenario: SR-SQL-005 — The saved query is checked again every time it is run
    Given a saved Request
    When it is run from anywhere
    Then the query stored on it is checked again before it is run
    And a stored query that no longer passes is refused rather than run

  Scenario: SR-RUN-006 — A Request answers with the Cards it names, in its own order, each one once
    Given a Request whose query says what order it wants
    When it is run
    Then the Cards come back in that order
    And a Card the query named twice appears once
    And an archived Card is not in the answer
    And a Card that no longer exists is skipped, rather than breaking the run

  Scenario: SR-AI-007 — The screen shows the query that will be saved, and a bad one never gets there
    Given Safwa proposes a Request with a query
    When the proposal is put together
    Then the query the owner reads on the screen is exactly the one Save will store
    And a query that does not pass comes back to Safwa as something it can fix and try again
    And for that refused attempt there is no proposal and no Request anywhere

  Scenario: SR-AI-009 — A Request is archived, never deleted
    Given Safwa wants a Request gone
    When it asks for it to be removed
    Then deleting it outright is refused, because only a Card can be deleted
    And once the owner approves archiving it, it is gone from the Request screens, and Safwa no
      longer sees it
    And a Plan filter that was using it stops using it, without an error anywhere
    And its name is free for a new Request to take

  Scenario: SR-AI-010 — A Request may be renamed without a screen; it may never be re-aimed without one
    Given Safwa proposes a change to a Request
    When it is considered for saving without asking the owner
    Then a change to its name or its description may go through
    And any change that touches the query always goes to the owner first
    And a brand new Request always goes to the owner first

  Scenario: SR-READ-011 — Safwa reads the live Requests, and points at one with its count
    Given saved Requests exist
    When Safwa answers
    Then it sees the ones that are not archived, and only those
    And it can point at one in its answer
    And what the owner reads on it is the Request's name and how many Cards it returns right now
    And pointing at an archived Request leaves the words and takes the link away
    And so does pointing at a number that is no Request at all

  Scenario: SR-UI-012 — /requests is a list, a run, and a way back
    Given the owner opens /requests
    Then they see every Request that is not archived, by name
    When they open one
    Then it runs, and shows its description and how many Cards it matched
    And the first 25 of those Cards are there to tap (REQUEST_RESULT_LIMIT = 25)
    And it says so when there were more than that
    And a Card opened from that list comes back to the Request it was opened from
    And Refresh runs the Request again
