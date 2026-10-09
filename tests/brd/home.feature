Feature: Home
  Home is the way in. It keeps no item of its own: it is the dashboard with the menu every
  other screen is offered from, the door a link Safwa wrote comes back through, and what a
  cleared chat is left with.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given each part of Safwa says what its own screen is called, and none of them says where

  Scenario: HM-MENU-001 — The menu is every screen that gave itself a name
    Given a part of Safwa that named its screen
    Then that name is a button on the menu
    And the buttons are in the one order Home keeps, which no other part knows
    And a screen that named itself and has no place in that order would be missing without a
      word, so the two lists are the same list
    And Home is not a button on its own menu: it is where the buttons are

  Scenario: HM-OPEN-002 — A link Safwa wrote opens the item, and leaves the answer alone
    Given Safwa's answer named a Card, and the owner taps it
    Then that Card's own screen arrives as a new message, and the menu does not
    And the answer the link was in is still there, word for word
    When the link names an item that is gone
    Then Safwa says it could not be opened, and nothing else changes
    When the link names something that is not one of Safwa's items
    Then Safwa says so

  Scenario: HM-QUIET-003 — A quiet chat with Secret word off is cleared down to Home and what Safwa said unasked since
    Given the owner has not written, spoken, sent a photo or pressed a button for 30 minutes (HOME_AFTER_MINUTES_DEFAULT = 30)
    And the chat is free: no answer is being written and no review is waiting
    Then the Home dashboard is drawn once its words are written (HM-VALUES-007), and every message before it is cleared: screens, receipts, the owner's messages and the Advisor's replies (TG-HOME-023)
    But a Reminder or anything else Safwa said unasked after the owner last wrote to the Advisor stays
    And it stays through later clears until the owner sends another message to the Advisor
    When a review is waiting
    Then nothing is cleared until it is answered or closes by itself
    When the owner acts while the chat is being cleared
    Then the clearing stops, and what they did is taken up
    When nothing came into the chat and the owner did nothing since the dashboard was drawn
    Then it is left as it is

  Scenario: HM-QUIET-004 — A dashboard drawn on an earlier day is drawn again
    Given the dashboard in the chat was drawn on an earlier day
    When the owner is quiet and the chat is free
    Then a new dashboard is drawn and the old one is taken out, as by HM-QUIET-003
    And a day is the workspace's local day

  Scenario: HM-STAYS-005 — Home follows the same one-screen rule as every other screen
    Given the dashboard a clear drew is in the chat
    Then it carries one button, ☰ Menu
    When the owner presses it
    Then Home is taken out and the dashboard with the menu opens as the only screen
    When the owner opens a screen from that menu
    Then that screen replaces the menu dashboard
    When the owner sends /start, writes a message, opens another screen or taps a link on Home
    Then the old dashboard is taken out, and only the newly opened screen remains
    And the conversation still starts after the clear, even though Home is no longer visible

  Scenario: HM-ACTIONS-006 — The dashboard shows every Action in Today
    Given open Actions in Today
    Then every one of them is on the dashboard, in Today's order (PL-KEY-025), each a link that opens it
    And the heading says Today, and how many Card rows it holds
    And Today shows planned execution counts, and EP load while enabled, including repeats (PL-REPEAT-031, PL-REPEAT-033)
    And an Action under a Goal is shown under its root Goal, skipping every nested Goal between them
    And the Goals come in the order of their first Action, and the Actions with no Goal after them
    When Today holds none
    Then the dashboard says nothing is planned for today, and no Sprint or Backlog is shown in its place

  Scenario: HM-VALUES-007 — Each Value in focus carries a few words written for it
    Given Values in focus
    Then each is on the dashboard as a link that opens it, with one or two sentences under it (MOTIVATION_MAX_CHARS = 200)
    And the words for every Value are written in one request with reasoning off, from those Values, About me, the open Goals in priority_goals order with their priorities and deadlines, the current Sprint's Success criteria, Today Actions in Today's order, the 10 Actions finished last (MOTIVATION_DONE_ACTIONS = 10) and the 2 last Diary entries with words (MOTIVATION_DIARY_ENTRIES = 2), however long ago
    And with no Sprint running, the session says so instead of reading the next Sprint's draft criteria
    And higher-ranked Goals relevant to a Value and Today Actions that serve the Sprint's criteria guide the words
    And the next request reads the last 5 successful generations by Value (MOTIVATION_HISTORY_SIZE = 5), newest first, and asks for a different angle and wording without repeating or merely rephrasing their message
    And this history lasts until the bot restarts; fresh words reused and failed requests do not add to it
    And a dashboard drawn within 10 minutes of the words being written shows them again without a request (MOTIVATION_FRESH_MINUTES = 10)
    And a dashboard that needs words while a request runs waits for that request and makes none of its own
    And the request runs to its end when the dashboard that asked for it is gone
    When the words for a Value were not written
    Then it shows its name alone
    And when no words were written at all, none are kept, and the next dashboard makes a request
    And a Value not in focus is not on the dashboard

  Scenario: HM-TIME-008 — With Time tracking on, the dashboard shows the time of the day
    Given Time tracking is on in the Profile
    Then the dashboard shows the time of the Actions finished today, added up as "2h 15m", and how many Actions it came from
    And with none, it says nothing is tracked yet
    When Time tracking is off
    Then the dashboard shows no time
    And a day is the workspace's local day

  Scenario: HM-LOG-009 — The dashboard opens with the last 5 changes
    Given saved changes to the workspace
    Then the last 5 are on the dashboard, newest first (HOME_LOG_SHOWN = 5), one line each: when, which item, and what changed
    And an item that still exists is a link that opens it

  Scenario: HM-HISTORY-010 — After a clear Safwa starts the conversation over
    Given the chat was cleared through the last message to the Advisor and the dashboard was drawn
    Then the conversation Safwa reads begins after the dashboard (TG-HOME-023)
    And the Diary still reads everything said that day, before the clear as much as after it (DI-READ-016)

  Scenario: HM-START-011 — /start opens Home at once: the dashboard with its menu unfolded
    When the owner sends /start, or presses ↩️ Menu on a screen
    Then Home arrives at once as a screen: the dashboard of HM-ACTIONS-006 to HM-LOG-009 with
      every menu button under it (HM-MENU-001)
    And ↩️ Menu redraws its screen as Home in place
    And each Value in focus carries the words written within the last 10 minutes (HM-VALUES-007)
    When no such words are kept
    Then each Value shows its name alone while the words are written
    And when they come, Home is redrawn in place with them, and its buttons stay as they were
    When the owner acts before the words come, or the message no longer shows that Home
    Then the message stays as it is, and the words are kept for the next dashboard
    When the owner sends /start again while the words are written
    Then one request is made, and only the last Home is redrawn with its words
    And earlier screens are removed, while what was said stays and the conversation Safwa reads goes on
    And the dashboard with ☰ Menu alone is drawn only by a clear (HM-QUIET-003, HM-CLEAR-012)

  Scenario: HM-CLEAR-012 — /clear clears the chat at once, as a quiet chat is cleared
    When the owner sends /clear
    Then the chat is cleared and the dashboard drawn as by HM-QUIET-003 at once, without waiting
      for the quiet time or for the words
    And its words come as on /start (HM-START-011), under its one ☰ Menu
    When the owner acts while the dashboard is being drawn
    Then the clearing stops, and what they did is taken up

  Scenario: HM-GOALS-013 — The dashboard names the first 5 Priority Goals
    Given open Goals
    Then the first 5 are on the dashboard (HOME_GOALS_SHOWN = 5), in the order Safwa is handed them (WS-CONTEXT-008), each a link that opens it
    When no Goal is open
    Then the dashboard has no Goals block

  Scenario: HM-ORDER-014 — The dashboard reads in one order
    Given changes, open Goals, Values in focus, Actions in Today, and Time tracking on
    Then the dashboard reads, top to bottom: the day and the time it was drawn, the last changes, the Priority Goals, the Values in focus, Today, and the time tracked today

  Scenario: HM-LOCK-015 — Automatic clearing leaves a protected chat empty until the word is entered
    Given Secret word is set in Profile
    When Home after elapses and the chat is free
    Then every deletable message is removed, including unanswered hooks, and no Home is sent
    And unanswered hooks are stored to be returned, while new hook answers are prepared unseen
    When the correct word is entered
    Then attempts and prompts are deleted, the stored hooks appear in order and Home is last
    And access remains open until the next automatic clear
    But /clear while access is open still draws Home and does not lock access
