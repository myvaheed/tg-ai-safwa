Feature: Tags
  A Tag is a label the owner puts on Cards so they can find them together later. It has a name, a
  description, and nothing else — no focus, no meaning Safwa is asked to weigh, and it never goes on
  anything but a Card.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where a Tag can be written by the owner or proposed by Safwa

  Scenario: PL-TAG-015 — A Tag is a label, and one Tag is on many Cards
    Given a Tag named "Family"
    When the owner puts it on a phone call, a trip plan and a birthday
    Then all three carry Family, and Family is on all three
    And that is what a Tag is for: finding those Cards together later
    And taking Family off one of them leaves it on the other two
    And the Tag's screen says how many Cards carry it right now
    And a Tag has no focus, and a Tag never goes on a Check — a Tag is for finding Cards

  Scenario: PL-TAG-016 — A Tag's name is taken whatever the capitals, and is never blank
    Given a Tag named "Family"
    When a second Tag is written as "FAMILY"
    Then it is refused, because that name is taken
    And renaming another Tag to "family" is refused the same way
    And a name that is empty, or only spaces, is refused

  Scenario: PL-TAG-017 — Writing down a Tag the owner archived brings that one back
    Given a Tag named "Family" was archived, and it had a description
    When a Tag is written down under that name again, in any capitals
    Then it is the archived one that comes back, still the same Tag
    And it keeps the description it had, unless a new one was given
    And a description nobody typed is not a new one
    And it comes back on no Cards, because archiving took it off the ones it was on
    And there is still only one Family

  Scenario: PL-TAG-018 — Archiving a Tag takes it off its Cards, and those Cards stay
    Given a Tag that is on some Cards
    When the owner archives it
    Then they are asked first, and told how many Cards it is about to come off
    And it comes off all of them at the moment it is archived
    And those Cards are otherwise untouched: none of them is deleted, moved or changed
    And a Tag that is already archived cannot be archived again

  Scenario: PL-TAG-019 — A Tag is archived, never deleted
    Given something wants a Tag gone
    Then archiving is the only way it goes
    And a request to delete one is refused before anything is written
    And the owner's screen offers Archive and no delete either

  Scenario: PL-TAG-020 — A Card cannot be given a Tag that is archived
    Given a Tag the owner archived
    When something tries to put it on a Card, by its name or by its number
    Then it is refused
    And a name that belongs to no Tag is refused too, and says which name it was
    And nothing is half-done: if one name in a link cannot be found, none of them are linked

  Scenario: PL-TAG-021 — Choosing Tags for a Card shows them a page at a time
    Given a Card is being tagged and there are more Tags than fit on a page
    When the list of Tags opens
    Then it shows 10 of them and says which page this is (SELECTOR_PAGE_SIZE = 10)
    And Next reaches the rest, so no Tag is out of reach
    And ticking one on the second page leaves the owner on the second page
    And Back returns to the Card the list was opened from
