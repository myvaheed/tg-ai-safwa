Feature: Tags
  A Tag is a label the owner puts on Cards so they can find them together later. It has a name, a
  description, and nothing else — no focus, no meaning Safwa is asked to weigh, and it never goes on
  anything but a Card.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where a Tag can be written by the owner or proposed by Safwa

  Scenario: TA-LINK-001 — A Tag is a label, and one Tag is on many Cards
    Given a Tag named "Family"
    When the owner puts it on a phone call, a trip plan and a birthday
    Then all three carry Family, and Family is on all three
    And that is what a Tag is for: finding those Cards together later
    And taking Family off one of them leaves it on the other two
    And the Tag's screen says how many Cards carry it right now
    And a Tag has no focus, and a Tag never goes on a Check — a Tag is for finding Cards

  Scenario: TA-NAME-002 — A Tag's name is taken whatever the capitals, and is never blank
    Given a Tag named "Family"
    When a second Tag is written as "FAMILY"
    Then it is refused, because that name is taken
    And renaming another Tag to "family" is refused the same way
    And a name that is empty, or only spaces, is refused

  Scenario: TA-PICK-007 — Choosing Tags for a Card shows them a page at a time
    Given a Card is being tagged and there are more Tags than fit on a page
    When the list of Tags opens
    Then it shows 10 of them a page, by SC-PAGE-007 (SELECTOR_PAGE_SIZE = 10)
    And ticking one on the second page leaves the owner on the second page
    And Back returns to the Card the list was opened from

  Scenario: TA-DELETE-008 — A Tag is deleted, not archived
    Given a Tag on several Cards
    When the owner or Safwa asks for it to be removed
    Then it is deleted, and those Cards lose the label and are otherwise untouched
    And no screen offers to archive a Tag, and the remove tool refuses to
    And its name is free from that moment

  Scenario: TA-CONTEXT-009 — Safwa is handed every Tag there is, so it labels in the owner's own words
    Given the owner keeps Tags
    When Safwa answers them
    Then it has already been handed all of them, by name and in alphabetical order
    And each arrives as a link it can hand straight back to the owner
    And none is left out: a Tag has no focus to be out of, which is what makes it unlike a Value
