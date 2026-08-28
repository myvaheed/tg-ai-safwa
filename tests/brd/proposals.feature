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

  Scenario: PR-WRITE-002 — Safwa proposes, and only the owner writes
    Given the owner asks Safwa to change something on their board
    When Safwa decides what should be done
    Then nothing is written: a review screen appears with the proposal on it
    And what it proposes happens only once the owner has saved it
    And the tool that proposed it belongs to a subagent, never to Safwa's own voice

  Scenario: PR-SCREEN-003 — A review screen shows everything the proposal would do, behind Save and Discard
    Given a review screen is open on a proposal
    When the owner looks at it
    Then it names the item and lists what would change, field by field
    And it carries exactly two buttons, Save and Discard
    When the proposal holds more than one edit
    Then every one of them is listed on that same screen, still behind one Save and one Discard

  Scenario: PR-SCREEN-004 — Deleting a Card asks once more before it goes
    Given a proposal to delete a Card
    When the owner presses Save
    Then nothing is deleted yet: one more screen asks to confirm, saying the whole tree under that
      Card and its contribution to past totals go with it
    And the Card is deleted only after the owner presses that confirmation
    And no other proposal asks twice

  Scenario: PR-QUEUE-005 — Whatever Safwa proposes at once is one screen
    Given the owner asks for something that needs the board changed
    When Safwa proposes an edit to one item
    Then it becomes one proposal with one review screen, whether it sets one field or five
    And whether Safwa proposes two edits to one item together or apart is its own choice,
      and no rule here decides it

  Scenario: PR-QUEUE-006 — Several proposals from one request are reviewed one at a time, in the order Safwa made them
    Given one request from the owner needs Safwa to propose three times
    When the proposals are put together
    Then there are three review screens, not one screen listing three items
    And they are queued in the order Safwa made them, not sorted or grouped
    And each is headed with its place in the queue, "Proposal 2/3"
    And a proposal that is alone in its queue carries no such heading

  Scenario: PR-QUEUE-007 — Only the front of the queue is on screen, and Safwa answers after the last one
    Given three proposals from one request are queued
    When the owner saves or discards the one on screen
    Then the next one in the queue takes its place on screen
    And Safwa says nothing about the request while any of them is still undecided
    And once the last one is decided, Safwa is given every decision at once and then answers

  Scenario: PR-QUEUE-008 — Saving one proposal does not spoil the ones behind it
    Given Safwa proposed three edits to the same Card, and all three are queued
    When the owner saves the first one
    Then the second one is still saveable, and saving it edits the Card as it now is
    And the third one is still saveable after that

  Scenario: PR-SAVE-009 — Save writes through the same operations the manual screens use
    Given a proposal to move an Action to Done
    When the owner saves it
    Then the Action moves exactly as it would if the owner had pressed Done on its own screen
    And everything that follows from that follows too, its Card tree and its Sprint included
    And the proposal is recorded as saved, and its screen can no longer be acted on
    When a proposal holds more than one edit
    Then they are applied in the order the review screen listed them

  Scenario: PR-SAVE-010 — Discard writes nothing, and the rest of the request goes on
    Given a proposal is on screen and two more from the same request are queued behind it
    When the owner discards it
    Then nothing about that item changed
    And the proposal is recorded as discarded, and its screen can no longer be acted on
    And the two behind it are still queued and still shown in turn

  Scenario: PR-RESULT-011 — After each decision Safwa is told what became of that proposal
    Given the owner saved one proposal and discarded another from the same request
    When Safwa carries on with that request
    Then for each one it is told the decision, which item it was and what it would have done
    And for the saved one it is told not to propose it again
    And for the discarded one it is told that it did not happen

  Scenario: PR-STALE-012 — A proposal is refused once the board has moved on without it
    Given a proposal is on screen, and the owner leaves it unanswered overnight
    When the Sprint reaches its planned end and closes itself in the night
    And the owner presses Save in the morning
    Then nothing at all is written
    And the owner is told the board has moved on since Safwa proposed this, and it has to be
      proposed again
    And the screen cannot be acted on any more

  Scenario: PR-STALE-013 — A proposal too old to save says so when Save is pressed
    Given a proposal was made more than a day ago and was never answered
      (PROPOSAL_EXPIRY_HOURS = 24)
    When the owner presses Save
    Then nothing is written
    And the owner is told the proposal is too old to save and has to be proposed again
    And the screen cannot be acted on any more
    When Safwa restarts instead, with such a proposal still unanswered
    Then it is already too old to save before the owner touches anything
    And a proposal made 23 hours ago is left alone and is still answerable

  Scenario: PR-FAIL-014 — A proposal that fails while it is being saved takes nothing else with it
    Given three proposals from one request are queued and the second one cannot be applied
    When the owner saves it
    Then it is recorded as failed and nothing was written for it
    And the first one, already saved, stands
    And the third one is still queued
    And Safwa is told to fix only that one and try it once more
    When the same failure happens to a proposal no request is waiting on
    Then the proposal stays as it was and its screen comes back with the reason on it

  Scenario: PR-REPAIR-015 — A tool call Safwa could not turn into a proposal comes back as an error, not a screen
    Given one request where two of Safwa's three calls are well formed and the third is not
    When the proposals are put together
    Then the two well-formed ones are queued for review
    And the third opens no screen at all
    And Safwa is told what was wrong with it and what to send instead
    And Safwa is told its other calls were not cancelled and are waiting for the owner

  Scenario: PR-REPAIR-016 — After five tries Safwa stops rather than keep guessing
    Given Safwa keeps sending a call it cannot get right (MAX_REPAIR_ROUNDS = 5)
    When the fifth attempt fails as well
    Then Safwa stops trying and says it could not prepare that one
    And it says that nothing unfinished was applied
    And whatever it did get right in the same request is untouched

  Scenario: PR-INTERRUPT-017 — Writing instead of answering ends the review
    Given a review screen is in the chat and two more proposals from that request are queued behind
      it
    When the owner writes a new message instead of pressing Save or Discard
    Then none of the three is written
    And each of them is recorded as discarded
    And the screen loses its buttons and becomes a plain account of what the request did
    And the message they wrote is answered as their next request

  Scenario: PR-INTERRUPT-018 — What was already saved is still reported as saved
    Given three proposals from one request, the first of them already saved
    When the owner writes a message instead of deciding the second
    Then the first one stays saved
    And the account left in place of the screen names all three, the saved one included

  Scenario: PR-AUTO-024 — Autoapproval saves a change with no screen when it is exactly what was asked for
    Given autoapproval is switched on
    And the owner asked for a change to an item they already have
    When Safwa proposes that change, and both the operation and every field it sets are ones
      autoapproval is allowed to save
    Then the proposal is read against the owner's own words, and saved only if it is exactly what
      they asked for
    And it is saved with no screen ever shown, stored and recorded the way one the owner saved by
      hand is
    And Safwa is told autoapproval saved this one and that the owner decided nothing

  Scenario: PR-AUTO-025 — Autoapproval never covers a new item, and never an unlisted change
    Given autoapproval is switched on
    When Safwa proposes to create an item the owner does not have yet
    Then its review screen is shown, and the proposal is never read against their words
    When Safwa proposes an operation that is not on the list, or one that sets a field outside what
      that operation may set
    Then that one is shown as well, and it is not read against their words either

  Scenario: PR-AUTO-026 — Doubt leaves the proposal exactly as it was
    Given autoapproval read a proposal that could be read in more than one way
    When it decides the owner should see it
    Then the proposal stays exactly as it was prepared, and its review screen is shown
    When autoapproval cannot reach a decision at all
    Then the review screen is shown for that reason instead, and nothing about the proposal changed

  Scenario: PR-AUTO-027 — In a queue, autoapproval reads one proposal at a time, at the front
    Given one request from the owner becomes three proposals, queued in the order Safwa made them
    When autoapproval saves the first and the second has to be shown
    Then the second is on screen, and the third has not been read by autoapproval at all
    When the owner decides the one on screen
    Then the third is read then, and autoapproval may still save it with no screen of its own
