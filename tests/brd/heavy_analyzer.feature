Feature: The helper the Advisor calls

  The Advisor reads well and joins badly. A read that goes past one flat scan says so in its
  own result, and offers a helper that writes the query instead. The helper answers with the
  rows it read, never with words about them.

  Scenario: HAN-OFFER-001 — A complex read offers the helper
    Given the Advisor is answering the owner
    When it runs a read that joins views, groups rows, nests a query, or opens with WITH
    Then the result carries a notice naming call_helper
    And call_helper is on its tool list for the rest of the session

  Scenario: HAN-OFFER-002 — A simple read offers nothing
    Given the Advisor is answering the owner
    When it runs a read over one view that only filters and counts
    Then the result carries no notice about a helper
    And call_helper is not on its tool list

  Scenario: HAN-OFFER-003 — A result that was cut offers the helper
    Given a read matches more rows than its budget holds (DEFAULT_ROW_LIMIT = 50)
    When the Advisor runs it
    Then the notice that says the result was cut also names call_helper

  Scenario: HAN-OFFER-004 — A read that failed does not offer the helper
    Given the Advisor runs a read the database refuses
    When it reads the error
    Then it is told to fix that one SELECT
    And nothing in the error names call_helper

  Scenario: HAN-OFFER-005 — The offer outlives a screen the Advisor opened
    Given the Advisor was offered the helper
    And it then routed a change that opened a screen
    When the owner saves it and the Advisor's session resumes
    Then call_helper is still on its tool list

  Scenario: HAN-ASK-006 — The helper is given the question and the conversation
    Given the Advisor calls call_helper with a question of its own
    When the helper starts
    Then it is given the same conversation a routed subagent is given
    And the Advisor's question straight after it, as its own block

  Scenario: HAN-ASK-007 — The helper forwards a result and never retells it
    Given the helper has read what answers the question
    When it ends the session
    Then the Advisor is given those rows and the query that produced them
    And nothing the helper wrote in words reaches the Advisor

  Scenario: HAN-ASK-008 — Nothing happens while the helper runs
    Given the Advisor has called the helper
    Then the owner's turn is still running and no screen is shown
    And owner text that arrives is queued, not answered
    When the owner cancels the generation
    Then the helper stops with the request it belongs to

  Scenario: HAN-ASK-009 — The helper changes nothing
    Given the owner's question needs a change as well as an answer
    When the helper is asked
    Then it holds no tool that changes anything
    And the change is still the Advisor's to route

  Scenario: HAN-ASK-010 — A helper is called, never routed
    Given the Advisor's routing rules
    Then they name no helper
    When the Advisor routes to a helper by name
    Then it is refused and told which subagents it may route to

  Scenario: HAN-ASK-011 — A helper that gets nowhere does not cost the turn
    Given the helper spends its whole budget without an answer (HEAVY_ANALYZER_MAX_TOOL_CALLS = 10)
    When the Advisor reads the result
    Then it carries one sentence saying the helper could not work it out
    And the Advisor still answers the owner

  Scenario: HAN-READ-012 — The event log is read by the helper and by nobody else
    Given the owner asks how much of a stretch of work they closed themselves
    When the helper reads the log of changes to Cards
    Then each change says whether the owner made it on a screen or an approved proposal did
    And neither the Advisor nor the board is told that log exists
