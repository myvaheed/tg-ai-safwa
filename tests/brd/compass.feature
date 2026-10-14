Feature: Compass
  Each feature words what makes a good one of its items: Values and Value links, Goals and
  Actions, Checks. The check on each new item, every prompt that writes items and Compass read
  that one wording; Compass explains it when asked.

  Scenario: CMP-METHOD-001 — One wording of the method is read everywhere
    Given the method for Values and Value links, Goals and Actions, and Checks, each worded by its own feature
    Then each check on a new item reads that item's criteria and examples from it
    And the Advisor and every subagent that writes items read what each item is and how to write one from it
    And asked what makes a good Value, Goal, Action, Check or Value link, or how to word one, the onboarding subagent reads that whole part of the method before it answers
    And a topic it does not know is refused with the topics it does
