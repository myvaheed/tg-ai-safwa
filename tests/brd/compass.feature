Feature: Compass
  Each feature words what makes a good one of its items: Values and Value links, Goals and
  Actions, Checks. The check on each new item, every prompt that writes items and Compass read
  that one wording; Compass explains it when asked. Compass also reads the owner's whole tree
  of Values, Goals, Actions and Checks and asks about what could be set better, when the owner
  asks and every nine days, and draws that tree as one picture.

  Numbers below name the constant they come from; the tests read the constant.

  Scenario: CMP-METHOD-001 — One wording of the method is read everywhere
    Given the method for Values and Value links, Goals and Actions, and Checks, each worded by its own feature
    Then each check on a new item reads that item's criteria and examples from it
    And the Advisor and every subagent that writes items read what each item is and how to write one from it
    And asked what makes a good Value, Goal, Action, Check or Value link, or how to word one, the onboarding subagent reads that whole part of the method before it answers
    And a topic it does not know is refused with the topics it does

  Scenario: CMP-ROUTE-002 — The Advisor hands a question about the owner's own tree to Compass
    Given the owner asks whether their own Values, Goals, Actions, Checks or the links between them are set right, which Value an item serves, or where an item belongs in the tree, or a request starts with "Compass."
    Then the Advisor routes to Compass
    And Compass reads Cards, Checks, Values and Tags and the method, and has no tool that changes anything
    And each step it is told what SQL finds: a root Goal that serves no Value, a Goal carrying four Values or more (GOAL_VALUES_MANY = 4), and a Value no open Goal, Action or Check carries
    And a Card carrying "Inbox", a finished or archived Card and an answered Check count for none of them
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

  Scenario: CMP-SKY-004 — Compass shows the tree as one picture
    Given the owner asks to see their tree, or the part of it one Value or one Goal is on, as a picture
    When the Advisor routes to Compass and it calls show_constellation
    Then one picture arrives in the chat as a file, at full size, kept in the chat and read as no words
    And the Values stand along its top as stars, each in a colour it keeps in every picture, with the Goals and Subgoals below them, each as high as its tree_level, and the Actions at the bottom
    And a line joins each parent to its child, and each Value to what carries it
    And a Card under several Goals is drawn once, with a line from each of them
    And a Card carrying "Inbox", an archived Card and an answered Check are not drawn; a finished Card is drawn faint only when asked for
    And detail goals leaves the Actions out, and detail all adds the Checks
    And for one Value it draws the Cards that carry it, their parents up to the root and everything under them, with only the Values those carry
    And for one Goal it draws that Goal's branch
    And what SQL finds for Compass is ringed in red
    And a Value or Goal that is not there, a finished Goal asked for without done, or a Value and a Goal together are refused with the call to make instead, and nothing is sent
    And with no Value and no open Goal or Action nothing is sent, and the answer says there is nothing to draw yet

  Scenario: CMP-SKY-005 — The Dashboard's 🌌 Constellation button sends the whole tree as one picture
    Given the owner opens 🗂 Dashboard
    When they press 🌌 Constellation
    Then the whole tree, drawn as Compass draws it, arrives as a file in place of the Dashboard
    And below it a message names it, says how many items are ringed in red, and leads back to the Dashboard
    And going back brings the Dashboard back below the file, which stays in the chat to be opened later
    And with nothing to draw it says so, and sends nothing
