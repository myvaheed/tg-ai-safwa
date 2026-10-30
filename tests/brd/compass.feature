Feature: Compass
  Each feature words what makes a good one of its items: Values and Value links, Goals and
  Actions, Checks, and Tags as advice. The check on each new item, every prompt that writes items and Compass read
  that one wording; Compass explains it when asked. Compass also reads the owner's whole tree
  of Values, Goals, Actions and Checks and asks about what could be set better, when the owner
  asks and every nine days, and draws the Values, one Value or one Goal's branch as a picture.

  Numbers below name the constant they come from; the tests read the constant.

  Scenario: CMP-METHOD-001 — One wording of the method is read everywhere
    Given the method for Values and Value links, Goals and Actions, Checks, and Tags, each worded by its own feature
    Then each check on a new item reads that item's criteria and examples from it
    And the Advisor and every subagent that writes items read what each item is and how to write one from it
    And asked what makes a good Value, Goal, Action, Check, Value link or Tag, or how to word one, the onboarding subagent reads that whole part of the method before it answers
    And the method for Tags is advice: no check on a new item reads it, and it says to keep the Tag the owner wants
    And a topic it does not know is refused with the topics it does

  Scenario: CMP-ROUTE-002 — The Advisor hands a question about the owner's own tree to Compass
    Given the owner asks whether their own Values, Goals, Actions, Checks or the links between them are set right, which Value an item serves, or where an item belongs in the tree, or a request starts with "Compass."
    Then the Advisor routes to Compass
    And Compass reads Cards, Checks, Values and Tags and the method, and has no tool that changes anything
    And each step it is told what SQL finds: a root Goal that serves no Value, a Goal carrying four Values or more (GOAL_VALUES_MANY = 4), and a Value no open Goal, Action or Check carries
    And a Thought, a finished or archived Card and an answered Check count for none of them
    And it also looks for a Value that is really a Goal, a practice or a rule, a reversed link, and two Values with one root
    And an Action carrying the same Value as a Goal above it is not a finding
    And it names at most three findings, the most important first (COMPASS_FINDINGS_MAX = 3), cites each item and asks one question about each
    And it offers every change as a possibility, and a change is proposed only once the owner agrees

  Scenario: CMP-REVIEW-003 — Every nine days in the Morning the whole tree is looked at
    Given the Morning time passes on a day a whole number of nine days after the workspace's first day (COMPASS_REVIEW_DAYS = 9), in Planning or in a Sprint
    When the hook's work runs
    Then one mini session reads every Value with its description, the open Goals, Subgoals and Actions under their parents with their Values and Notes, the open Checks with their Cards and Values, and what SQL found
    And it answers clear, or at most three findings, the most important first
    And a clear answer still keeps the first three of what SQL found
    And on any other day, on the workspace's first day, or with no Value and no open Goal or Action, no mini session runs
    And nothing found produces no request
    And one request hands the findings to Compass, which reads the current items, leaves out a finding that no longer holds, names each and asks whether to go through them together, changing nothing
    And the request waits like any hook request while access is locked
    And switching Compass review off in Profile → Hooks stops the review

  Scenario: CMP-SKY-004 — Compass draws the Values, one Value or one Goal's branch
    Given the owner asks to see their Values, one Value or one Goal as a picture
    When the Advisor routes to Compass and it calls show_constellation
    Then with no Value and no Goal named it draws the sky of Values (CMP-SKY-007)
    And with a Value named it draws that Value's orbits (CMP-SKY-008)
    And with a Goal or a Subgoal named it draws its branch (CMP-SKY-009)
    And one picture arrives in the chat as a file, at full size, kept in the chat and read as no words
    And each Value has one colour in every picture
    And a Thought, a finished or archived Card and an answered Check are not drawn
    And what SQL finds for Compass is ringed in red wherever it is drawn
    And a Value or Goal that is not there, or a Value and a Goal together, are refused with the call to make instead, and nothing is sent
    And a finished Goal is refused, and nothing is sent
    And with no Value and no open Goal or Action nothing is sent, and the answer says there is nothing to draw yet

  Scenario: CMP-SKY-005 — The Dashboard's 🌌 Constellation button opens the sky and leads down to a Value and a Goal
    Given the owner opens 🗂 Dashboard
    When they press 🌌 Constellation
    Then the sky of Values arrives as a file in place of the Dashboard
    And below it a message names it, says how many items are ringed in red, and has a button for each Value of weight above 0, in the owner's order, and one that leads back to the Dashboard
    And a Value's button sends its orbits below, with a message that has a button for each Goal on the nearest orbit and one that leads back
    And a Goal's button sends its branch below, with a message that leads back
    And ↩️ Back under any of them brings back, below the files, the screen 🌌 Constellation was pressed on; the files stay in the chat
    And with nothing to draw it says so, and sends nothing

  Scenario: CMP-WEIGHT-006 — A Value weighs as much as the open work that carries it
    Given open Goals, Subgoals, Actions and Checks
    Then an item counts for a Value when it, the Card it is on or a Goal above them carries that Value, once however many paths lead to it
    And a Goal or a Subgoal weighs 1 more than its tree_level
    And an Action or a Check weighs 1, and one that repeats weighs the square root of how many times its Schedule plans it in the next 28 days, and at least 1 (SKY_WINDOW_DAYS = 28)
    And one that repeats after each completion counts as planned 4 times (AFTER_COMPLETION_RUNS = 4)
    And a Value weighs the sum of what counts for it
    And a Thought, a finished or archived Card and an answered Check weigh nothing
    And Priority does not change a weight

  Scenario: CMP-SKY-007 — The sky shows each Value as a star as big as its weight
    Given the owner's Values
    When the sky of Values is drawn
    Then each Value is a star along one arc in the owner's order, with its rank and its weight under its name
    And a star's area grows with its weight, and a Value of weight 0 is a dim star ringed in red
    And a line joins two Values that open items count for together, thicker the more weight they share
    And no Goal, Action or Check is drawn

  Scenario: CMP-SKY-008 — One Value's orbits show what carries it and how much
    Given a Value of weight above 0
    When its orbits are drawn
    Then the Value is a star in the middle, its name heading the picture with its rank, its weight and its place by weight among the Values
    And every item that counts for it stands on an orbit around it
    And the highest items stand on the nearest orbit, each level below one orbit further out, and the Actions and Checks on the outermost
    And a solid line joins the star to each item that carries the Value itself, as thick as that item's weight with the weight of the items under it that do not carry the Value themselves
    And the solid lines' weights add up to the Value's weight
    And a dashed line joins each parent to its child
    And an item that carries other Values has a dot in each one's colour

  Scenario: CMP-SKY-009 — A Goal's branch reads from left to right
    Given an open Goal or Subgoal
    When its branch is drawn
    Then it stands at the left, and its open Subgoals, Actions and Checks stand to the right, each level one step further
    And a Card under several Goals of the branch is drawn once, with a line from each
    And the Goals above it, up to each root, stand dashed to its left along every path, without their other children
    And each Goal and Subgoal has an arc for the share of the one-off Actions under it that are Done
    And an item in the Sprint or Today glows, a repeating one is marked ↻, and a blocked Action is marked Blocked
    And each item has a dot in the colour of each Value it carries

  Scenario: CMP-STATS-010 — Each picture has a short panel of what was done and what is left
    Given the sky of Values, a Value's orbits or a Goal's branch
    Then a panel at its right counts the last 28 days for the sky and a Value (SKY_WINDOW_DAYS = 28), and all time for a Goal
    And the sky's panel says how many Actions were Done, how many Checks passed of those answered, how many Actions are open and how many of those are in the Sprint or Today, how many Done Actions no Value counts, and how many Values weigh 0
    And a Value's panel says the same of what counts for it, without the last two, and how many repeating Actions count for it and how many times a week they are planned
    And a Goal's panel says how many of its one-off Actions are Done out of all of them, as a share, how many are open and how many of those are Blocked, how many times its repeating Actions were Done, its Deadline with the days left, and the day it was created
    And each panel adds the Effort Points Done while Effort Points are on, and the time tracked while time tracking is on
    And a Goal's panel adds the Effort Points of its open Actions while Effort Points are on
