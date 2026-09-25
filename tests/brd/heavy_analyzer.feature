Feature: The helper the Advisor calls

  The Advisor reads well and joins badly. A read that goes past one flat scan is not run:
  what comes back names a helper that writes the query instead. The helper answers with the
  rows it read, never with words about them.

  Scenario: HAN-OFFER-001 — A complex read is not run, and the helper is offered instead
    Given the Advisor is answering the owner
    When it calls a read that joins views, groups rows, nests a query, or opens with WITH
    Then the read is not run, and what it reads back as the result is a notice naming call_helper
    And call_helper is on its tool list for the rest of the session

  Scenario: HAN-OFFER-002 — A simple read offers nothing
    Given the Advisor is answering the owner
    When it runs a read over one view that only filters and counts
    Then the result carries no notice about a helper
    And a clause's word inside a quoted value — a title with "with" in it — is not the clause
    And call_helper is not on its tool list

  Scenario: HAN-OFFER-004 — A read that failed does not offer the helper
    Given the Advisor runs a read the database refuses
    When it reads the error
    Then nothing in the error names call_helper, by AG-TOOL-033

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
    Given the helper spends its whole budget without an answer, by AG-HELPER-028
      (HEAVY_ANALYZER_MAX_TOOL_CALLS = 10)
    When the Advisor reads the result
    Then it carries one sentence saying the helper could not work it out
    And the Advisor still answers the owner

  Scenario: HAN-READ-012 — The event log is read by the helper and by Safwa itself
    Given the owner asks how much of a stretch of work they closed themselves
    When the helper reads the log of changes
    Then each change says whether the owner made it on a screen or an approved proposal did
    And the part that changes the workspace is not told that log exists
