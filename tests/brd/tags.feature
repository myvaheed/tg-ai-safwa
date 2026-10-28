Feature: Tags
  A Tag is a label the owner puts on Cards so they can find them together later. It has a name, a
  description, and no focus, and it never goes on anything but a Card. All Tags follow the same rules; no Tag is built in.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where a Tag can be written by the owner or proposed by Safwa

  Scenario: TA-LINK-001 — A Tag is a label, and one Tag is on many Cards
    Given a Tag named "Family"
    When the owner puts it on a phone call, a trip plan and a birthday
    Then all three carry Family, and Family is on all three
    And that is what a Tag is for: finding those Cards together later
    And taking Family off one of them leaves it on the other two
    And the Tag's screen sums up the Cards carrying it right now, by TA-LINK-012
    And a Tag has no focus, and a Tag never goes on a Check — a Tag is for finding Cards

  Scenario: TA-NAME-002 — A Tag's name is taken whatever the capitals, and is never blank
    Given a Tag named "Family"
    When a second Tag is written as "FAMILY"
    Then it is refused, because that name is taken
    And renaming another Tag to "family" is refused the same way
    And Unicode case is treated the same: "Семья" takes "СЕМЬЯ", and "Straße" takes "STRASSE"
    And a name that is empty, or only spaces, is refused

  Scenario: TA-PICK-007 — Choosing Tags for a Card shows them a page at a time
    Given a Card is being tagged and there are more Tags than fit on a page
    When the list of Tags opens
    Then it shows 10 of them a page, by SC-PAGE-007 (SELECTOR_PAGE_SIZE = 10)
    And ticking one on the second page leaves the owner on the second page
    And Back returns to the Card the list was opened from

  Scenario: TA-DELETE-008 — A Tag is deleted, not archived
    Given an ordinary Tag on several Cards
    When the owner or Safwa asks for it to be removed
    Then it is deleted, and those Cards lose the label and are otherwise untouched
    And no screen offers to archive a Tag, and the remove tool refuses to
    And its name is free from that moment

  Scenario: TA-CONTEXT-009 — Safwa is handed every Tag there is, so it labels in the owner's own words
    Given the owner keeps Tags
    When Safwa answers them
    Then it has already been handed all of them, by name and in alphabetical order
    And each arrives as a link it can hand straight back to the owner
    And none is left out
    And each Tag comes with its description on its own line

  Scenario: TA-DESC-011 — A Tag's description holds at most 400 characters
    Given a Tag
    When its description is written with 401 characters, on its screen or by Safwa (DESCRIPTION_MAX_CHARS = 400)
    Then it is refused with the limit and the length it has, and the Tag keeps the description it had
    And the screen's editor stays open with that message, and its instruction names the limit
    And Safwa's tag tool says the limit in the description field
    And a call of Safwa's over it comes back to Safwa refused, with no screen shown

  Scenario: TA-INBOX-010 — Inbox is an ordinary name, and no Tag is built in
    Given a new or an existing workspace
    When Safwa starts
    Then it creates no Tag named "Inbox"
    When the owner creates a Tag named "Inbox"
    Then it can be renamed, described, linked and deleted like every other Tag
    And Safwa can propose those changes with the ordinary tools
    And that name exempts no Goal or Action from a review

  Scenario: TA-LINK-012 — A Tag's Linked Cards read as a Goal's children do, and open as a list
    Given a Tag on a Goal and on an Action
    When its screen is opened
    Then "Linked Cards:" counts those Cards as CD-LINK-055 counts a Goal's children: "🎯1 · ⭐️1",
      with Done of all their Effort Points while Effort Points are on
    And it is a link that opens those Cards as a list, by CD-ORDER-054
    And ↩️ Back on that list returns to the Tag
    And a Tag no Card carries reads "Linked Cards: —", with no link

  Scenario: TA-EDIT-013 — A Tag proposal is edited with the Tag's own controls
    Given Safwa proposes to create or change a Tag
    When the owner presses "✏️ Edit" on its review screen
    Then each field the proposal sets has the button the Tag's own screen gives it: "✏️ Name" and
      "📝 Description"
    And each asks for the words the Tag's own screen asks for, and refuses what it refuses
