Feature: Proposals
  A proposal is how Safwa changes anything: the model never writes, it proposes, and the owner
  saves or discards. What a proposal may not reach is as much a rule as what it writes.

  Background:
    Given a workspace where every change Safwa makes is a proposal the owner decides on

  Scenario: PR-TARGET-001 — An archived item is not changed automatically
    Given an archived Card and an archived Check
    When Safwa proposes a change to either
    Then it is refused, and the refusal says an archived item is not changed automatically
    And Safwa ends its answer citing each one, for the owner to open and change by hand
    When Safwa proposes a change to an id that matches nothing
    Then it is refused for that reason instead, and Safwa is sent to find the id
