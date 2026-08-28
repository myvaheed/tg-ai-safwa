Feature: Screens
  A screen is a bot message the owner can act on: a dashboard, a Card editor, a review screen.
  Telegram is one column of messages, so a screen is only ever the last thing in the chat. What
  the owner does next is below it, and a screen they left behind is a control for a state that has
  moved on.

  Background:
    Given a chat where every screen the owner can act on carries its own buttons

  Scenario: SC-LIVE-001 — Only one screen is live, and it is the one the owner just acted on
    Given a dashboard, a Card editor and a review screen were all opened earlier in the conversation
    When the owner writes a message, runs a command, or walks into another screen from a menu
    Then every screen except the one that action belongs to stops being answerable
    And a dashboard or an editor is taken out of the chat, while a review screen stays as a written
      account of what became of it
    And the screen that action belongs to is left alone
    When the owner presses a button that only redraws the screen they are already on, such as
      turning its page
    Then no screen is ended by that
