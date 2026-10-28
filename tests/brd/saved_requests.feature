Feature: Saved Requests
  A Request is a saved question about the owner's Cards, Checks and Values: a name, a description, and a query Safwa
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
    And the only way one comes into being is the owner approving a proposed Request,
      or the defaults supplied at installation, by SR-UI-013
    And the part of Safwa that keeps the workspace is what proposes one; the part that talks to the
      owner never does

  Scenario: SR-WRITE-002 — A Request's name is taken whatever the capitals, and is never blank
    Given a Request named "All goals"
    When a second one is saved as "ALL GOALS"
    Then it is refused, because that name is taken
    And renaming another Request onto that name is refused the same way
    And a name that is empty, or only spaces, is refused

  Scenario: SR-SQL-004 — A Request's query only reads, and it has to come back with Cards, Checks or Values
    Given a Request is being saved
    When its query is checked
    Then it is checked the way every read Safwa makes is checked, by AG-READ-027
    And it must ask about Cards, Checks or Values
    And each result names its type, Card, Check or Value, and its id
    And anything else is refused, and no Request is written

  Scenario: SR-SQL-005 — The saved query is checked again every time it is run
    Given a saved Request
    When it is run from anywhere
    Then the query stored on it is checked again before it is run
    And a stored query that no longer passes is refused rather than run

  Scenario: SR-RUN-006 — A Request answers with the Cards, Checks and Values it names, each one once
    Given a Request whose query says what order it wants
    When it is run
    Then the Cards, Checks and Values come back in that order
    And each item appears once, even if the query named it twice
    And a Card, a Check and a Value with the same id remain three different items
    And an archived Card or Check comes back marked "[📦]"
    And an item that no longer exists is skipped, rather than breaking the run

  Scenario: SR-AI-007 — The screen shows the query that will be saved, and a bad one never gets there
    Given Safwa proposes a Request with a query
    When the proposal is put together
    Then the query the owner reads on the screen is exactly the one Save will store
    And a query that does not pass comes back to Safwa as something it can fix and try again
    And for that refused attempt there is no proposal and no Request anywhere

  Scenario: SR-AI-010 — A Request may be renamed without a screen; it may never be re-aimed without one
    Given Safwa proposes a change to a Request
    When it is considered for saving without asking the owner
    Then a change to its name or its description may go through
    And any change that touches the query always goes to the owner first
    And a brand new Request always goes to the owner first

  Scenario: SR-UI-013 — A new workspace starts with Requests, and a screen saying what they are
    Given a workspace being created for the first time
    Then it already has "Goals without children", "Goals without Values" and "Values without Goals"
    And they select open Goals with no direct child Actions or open Goals, open Goals with no direct Values,
      and Values with no direct open Goals
    And Goals include Subgoals, and Done and archived Goals are ignored in these conditions, including as children
    And Done and archived Actions still count as children
    And a Value linked only to Actions or Checks still has no Goals
    And they are ordinary Requests from that moment: renaming, re-aiming and deleting them work
      as they do for any other, and once deleted it is not written again on the next start
    And the Requests list offers "О Запросах", which says what a Request is, that Safwa is what
      writes one, and that Tags are how Cards from different Goals are gathered
    And that screen goes back to the list

  Scenario: SR-READ-011 — Safwa reads the saved Requests, and points at one with its count
    Given saved Requests exist
    When Safwa answers
    Then it sees every Request the owner has saved
    And it can point at one in its answer
    And what the owner reads on it is the Request's name and how many Cards, Checks and Values it returns right now
    And so does pointing at a number that is no Request at all

  Scenario: SR-UI-012 — /requests is a list, a run, and a way back
    Given the owner opens /requests
    Then they see every Request the owner has saved, by name
    And each name shows how many Goals, Actions, Thoughts, Checks and Values it returns now as 🎯, ⭐️, 💭, ☑️ and 💎
    And zero categories are omitted; an empty Request shows 0
    And a Request that cannot run shows ⚠️ while the other Requests still show their counts
    When they open one
    Then it runs, and shows its description and how many Goals, Actions, Thoughts, Checks and Values it matched
    And those Cards use the common order of CD-ORDER-054 unless the query explicitly supplies
      its own ORDER BY, which the screen preserves across Cards, Checks and Values
    And without an explicit order the Checks and Values follow the Cards in query order
    And the first 25 of them are there to tap (RESULT_LIMIT = 25)
    And it says so when there were more than that
    And a Card, Check or Value opened from that list comes back to the Request it was opened from
    And Refresh runs the Request again

  Scenario: SR-DELETE-013 — A Request is deleted, not archived
    Given a saved Request that a Plan filter is using
    When the owner or Safwa asks for it to be removed
    Then it is deleted, and the filter stops using it without an error anywhere
    And no screen offers to archive a Request, and the remove tool refuses to
    And its name is free from that moment

  Scenario: SR-INBOX-014 — The Menu's Thoughts list replaces the capture Request
    Given the owner opens Thoughts from Menu
    Then it lists Thoughts oldest first, without requiring a saved Request
    And a Tag named "Inbox" on an Action does not put that Action in the selection
    And editing a Thought leaves it in the selection
    And a Thought opens its saved screen and comes back to this list
    And Refresh reads the list again and Menu returns to the menu
    And an empty list still offers Refresh and Menu
    And a new workspace has no default Request named "Thoughts"
