Feature: Saved Requests
  A Request is a name, a description and one read-only query over the `ai_*` views that the owner
  reruns from the interface. The owner never writes one: the model proposes it, the owner saves it,
  and from then on it is a button. Its result is always a list on a screen and never enters the
  model's history, so the query has to be valid and nothing else.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace whose only writer of Requests is the proposal path

  Scenario: SR-WRITE-001 — A Request exists only because the model proposed one and the owner saved it
    Given the owner is looking at their saved Requests
    When they look for a way to write one by hand
    Then there is none: the screens list, open and rerun Requests and nothing else
    And the only path that creates one is a `request` proposal the owner saved
    And that tool belongs to the board subagent, never to the Advisor

  Scenario: SR-WRITE-002 — A Request name is unique, and case is not a difference
    Given an active Request named "All goals"
    When a second Request is saved as "ALL GOALS"
    Then it is refused because a Request with that name already exists
    And renaming a different Request onto that name is refused the same way
    And a name that is empty or only spaces is refused

  Scenario: SR-WRITE-003 — Saving under an archived Request's name brings that one back
    Given a Request named "All goals" was archived, with a description
    When a Request is saved under that name again with new SQL
    Then the archived Request is the one that comes back, with its original id
    And its SQL is the new one
    And its description is the one it already had, because none was given
    And there is still exactly one Request with that name

  Scenario: SR-SQL-004 — A Request's query is one read-only SELECT, and it must ask for Card ids
    Given a Request is being saved
    When its SQL is checked
    Then it must be a single SELECT or WITH … SELECT over the `ai_*` views
    And it must mention `ai_cards`
    And it must return a column named `id`
    And anything else is refused, and no Request row is written

  Scenario: SR-SQL-005 — The stored statement is checked again every time it runs
    Given a saved Request
    When it is run from any surface
    Then the stored statement is validated again before it is executed
    And a stored statement that no longer passes is refused rather than run

  Scenario: SR-RUN-006 — A Request answers with the live Cards it names, in its own order, each one once
    Given a Request whose SQL orders its results
    When it is run
    Then the Cards come back in the order the query returned them
    And an id the query returned twice appears once
    And an archived Card is not in the answer
    And an id that is no longer a Card is skipped rather than failing the run

  Scenario: SR-AI-007 — The screen shows the statement Save will store, and SQL that fails never becomes a proposal
    Given the board subagent calls `request` with a `sql` field
    When the change is prepared
    Then the normalized statement is what the proposal row carries and the review screen prints
    And SQL that does not pass comes back as an `unsafe_query` tool error the model can retry
    And no proposal row and no Request exist for the refused call

  Scenario: SR-AI-009 — A Request is archived, never deleted
    Given the model wants a Request gone
    When it calls `remove` for that Request
    Then `delete` is refused at the contract, because only a Card allows it
    And an approved `archive` hides the Request from `ai_requests`, from the Request screens
    And a Plan filter that was picking it stops picking it, without an error
    And the Request's name is free for a new Request to reuse

  Scenario: SR-AI-010 — Autoapproval may rename a Request; it may never re-aim one
    Given the model proposes a change to a Request
    When autoapproval considers it
    Then a `name` or `description` update may be saved without a screen
    And a change that touches the SQL always takes the review screen
    And a Request creation always takes the review screen

  Scenario: SR-READ-011 — The Advisor reads Requests through the view and cites one with its live count
    Given saved Requests exist
    When the Advisor answers
    Then it reads them through `ai_requests`, which shows only the ones that are not archived
    And it cites one as `[name](request:2)`
    And the rendered link carries the Request's name and how many Cards it returns right now
    And a citation of an archived Request keeps its words and loses its link
    And so does a citation of an id that is no Request at all

  Scenario: SR-UI-012 — /requests is a list, a run and a way back
    Given the owner opens /requests
    Then they see every Request that is not archived, by name
    When they open one
    Then it runs and shows its description and how many Cards it matched
    And at most the first 25 of them are listed as buttons (REQUEST_RESULT_LIMIT = 25)
    And it says so when there were more
    And a Card opened from that list comes back to the Request it was opened from
    And Refresh runs the Request again
