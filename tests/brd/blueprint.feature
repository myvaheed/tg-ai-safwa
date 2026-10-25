Feature: Blueprint
  Blueprint is a conversation of its own inside the chat, where the owner works out a new
  Value, a Goal with its Subgoals and Actions, a habit tracker or a Tag, one question at a
  time. It changes nothing: the plan it agrees on comes back to the conversation, and the
  workspace proposes each of its items from there.

  Scenario: BP-ENTER-001 — A new item the owner asks to plan is worked out in Blueprint
    Given the owner asks to plan a new Value, Goal, habit tracker or Tag, or says yes when the Advisor offers to plan it
    Then the Advisor opens Blueprint, and the chat shows "📐 Blueprint" with how to end it with /exit
    And Blueprint answers first, reading the conversation it was opened from
    And for a Goal or Subgoal with an unclear result or completion criterion, the Advisor asks about it or offers to plan it, and opens nothing
    And an item the owner asks to create, without asking to plan it, goes to the workspace instead, by WS-SCOPE-001

  Scenario: BP-STEP-002 — Blueprint asks one thing at a time and builds each item from the owner's words
    Given Blueprint is open
    Then each of its messages asks one question
    And it asks which Value the item serves, offering the owner's Values by name, and works out a new one when none fits
    And a target, a number, a date or a time the owner did not say is never in the plan
    And it reads the workspace and has no tool that changes anything

  Scenario: BP-METHOD-003 — Blueprint holds each item to the method its feature words
    Given Blueprint shapes a Value, a Goal, an Action, a Check or a Tag
    Then its prompt holds the whole method of every item: what it is, its criteria, how to write one and its examples
    And it is the method the check on a new item reads (CMP-METHOD-001)
    And when a point of the method does not hold, it says which and asks
    And the method for a Tag is advice: Blueprint suggests it and keeps the Tag the owner wants

  Scenario: BP-PLAN-004 — An agreed plan comes back as one message
    Given the owner agrees to the plan, or wants to stop
    Then Blueprint ends the conversation with the plan as agreed, or with what was agreed so far
    And the conversation's stretch leaves the chat, and one message headed "📐 Blueprint" holds the plan
    And the plan has one line per item, each parent before its children: its mark, its title, new or a link to the existing item, the Goal it stands under or the Card it is on, and its Note, Deadline, Schedule, Values and Tags
    And its last line asks, in the owner's language, whether to create it

  Scenario: BP-CREATE-005 — Yes to a plan proposes each of its items
    Given a plan from Blueprint in the conversation
    When the owner says yes, or asks to create it
    Then the Advisor hands it to the workspace, and each new item of the plan comes on its own review screen
    And a Card under a new Goal of the plan is proposed once that Goal is saved, under that Goal's id
    And nothing of the plan is saved before the owner saves its screen
