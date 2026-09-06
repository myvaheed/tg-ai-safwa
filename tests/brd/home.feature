Feature: Home
  Home is the way in. It keeps no item of its own: it is the screen every other screen is
  offered from, and the door a link Safwa wrote comes back through.

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
