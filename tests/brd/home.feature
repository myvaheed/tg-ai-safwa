Feature: Home
  Home is the way in. It keeps no item of its own: it is the screen every other screen is
  offered from, the door a link Safwa wrote comes back through, and the dashboard a chat the
  owner left quiet is cleared down to.

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

  Scenario: HM-QUIET-003 — A chat the owner left quiet is cleared down to the Home dashboard
    Given the owner has not written, spoken, sent a photo or pressed a button for 30 minutes (HOME_AFTER_MINUTES_DEFAULT = 30)
    And the chat is free: no answer is being written and no review is waiting
    Then the Home dashboard is drawn, and the chat is cleared down to it (TG-HOME-023)
    And a message Safwa sent on its own while the owner was quiet, a Reminder included, is taken out with the rest
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

  Scenario: HM-STAYS-005 — The dashboard stays until the next clear
    Given the dashboard is in the chat
    When the owner writes, opens a screen or taps a link on the dashboard
    Then the dashboard stays where it is, and what comes of it appears below it
    And the dashboard carries no buttons
    And /start opens the menu, not the dashboard

  Scenario: HM-ACTIONS-006 — The dashboard opens with the next 5 Actions
    Given open Actions in Today
    Then the first 5 in Today's order (PL-KEY-025) are on the dashboard (HOME_ACTIONS_SHOWN = 5), each a link that opens it
    And the heading says Today, and how many Actions Today holds
    When Today holds none
    Then the first 5 of the Sprint are shown, in the Sprint's own order, under a heading that says Sprint
    And with the Sprint empty too, the first 5 of the Backlog, under a heading that says Backlog
    And an Action under a Goal is shown under that Goal, and a Subgoal between them is not shown
    And the Goals come in the order of their first Action, and the Actions with no Goal after them
    When Today, the Sprint and the Backlog are empty
    Then the dashboard says nothing is planned yet

  Scenario: HM-VALUES-007 — Each Value in focus carries a few words written for it
    Given Values in focus
    Then each is on the dashboard as a link that opens it, with one or two sentences under it (MOTIVATION_MAX_CHARS = 200)
    And the words are written as the dashboard is drawn, from that Value, About me, the titles of the open Goals, the 10 Actions finished last (MOTIVATION_DONE_ACTIONS = 10) and the 2 last Diary entries with words (MOTIVATION_DIARY_ENTRIES = 2), however long ago
    When the words for a Value could not be written
    Then it shows its name alone
    And a Value not in focus is not on the dashboard

  Scenario: HM-TIME-008 — With Time tracking on, the dashboard shows the time of the day
    Given Time tracking is on in the Profile
    Then the dashboard shows the time of the Actions finished today, added up as "2h 15m", and how many Actions it came from
    And with none, it says nothing is tracked yet
    When Time tracking is off
    Then the dashboard shows no time
    And a day is the workspace's local day

  Scenario: HM-LOG-009 — The dashboard ends with the last 10 changes
    Given saved changes to the workspace
    Then the last 10 are on the dashboard, newest first (HOME_LOG_SHOWN = 10), one line each: when, which item, and what changed
    And an item that still exists is a link that opens it

  Scenario: HM-HISTORY-010 — After a clear Safwa starts the conversation over
    Given the chat was cleared down to the dashboard
    Then the conversation Safwa reads begins after the dashboard (TG-HOME-023)
    And the Diary still reads everything said that day, before the clear as much as after it (DI-READ-016)
