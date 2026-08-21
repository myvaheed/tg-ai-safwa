Feature: Diary
  The Diary keeps one owner-written entry for each local calendar day.
  AI writes are proposals; the Advisor reads and opens saved days directly.

  Background:
    Given a fresh Diary workspace

  @di_day_001
  Scenario: DI-DAY-001 — Writing a missing day creates its single Diary entry
    Given no Diary entry exists for "2026-08-15"
    When the Diary writes "Первый день." with mood 8 for "2026-08-15"
    Then exactly one Diary entry exists for "2026-08-15"
    And its body is "Первый день."
    And its mood is 8

  @di_day_002
  Scenario: DI-DAY-002 — Writing an existing day replaces the whole entry
    Given a Diary entry "Уже записано." with mood 5 exists for "2026-08-15"
    When the Diary replaces it with "Переписал." and mood 7
    Then exactly one Diary entry exists for "2026-08-15"
    And its body is "Переписал."
    And its mood is 7
    And the entry keeps its identity and moves to the next version

  @di_date_003
  Scenario: DI-DATE-003 — A prepared write keeps the local date the owner named
    Given no Diary entry exists for "2026-08-14"
    When the Diary subagent prepares "Запись за вчера." for "2026-08-14"
    Then the prepared change targets "2026-08-14"
    And no Diary entry has been saved yet

  @di_mood_004
  Scenario: DI-MOOD-004 — A feeling score is optional and is bounded
    When the Diary validates an omitted mood and the boundary moods
    Then the omitted, zero, and ten moods are accepted
    When the Diary validates mood 11
    Then the invalid mood is rejected

  @di_delete_005
  Scenario: DI-DELETE-005 — An approved removal deletes the existing day
    Given a Diary entry "Есть что удалять." with mood 6 exists for "2026-08-15"
    When the owner approves deleting that Diary entry
    Then no Diary entry exists for "2026-08-15"

  @di_delete_005
  Scenario: DI-DELETE-005 — Removing an unwritten day is retryable
    Given no Diary entry exists for "2026-08-15"
    When the Diary subagent prepares a deletion for "2026-08-15"
    Then preparation reports "target_not_found"
    And the preparation result is retryable
    And no Diary entry has been saved yet

  @di_read_006
  Scenario: DI-READ-006 — The Advisor reads and cites a day without routing
    Given the Advisor Diary policy is loaded
    When the owner asks what was written on a saved Diary day
    Then the Advisor policy reads "ai_diary" directly
    And the Diary day can be cited as "(diary:12)"
    And only Diary writes require routing

  @di_link_007
  Scenario: DI-LINK-007 — A Diary citation identifies the complete read-only day
    Given a Diary entry "День с рынком." with mood 6 exists for "2026-08-15"
    When the Diary citation is made for that entry
    Then it identifies the Diary entry by its real id
    And its label includes the local date and optional mood

  @di_write_008
  Scenario: DI-WRITE-008 — An AI write is prepared as a proposal before it is saved
    Given no Diary entry exists for "2026-08-15"
    When the Diary subagent prepares "День до сохранения." for "2026-08-15"
    Then the prepared change is a create proposal
    And no Diary entry has been saved yet

  @di_receipt_009
  Scenario: DI-RECEIPT-009 — A Save or Discard receipt does not copy the day text
    When the Diary prepares a receipt for "Секретный текст дня."
    Then the receipt identifies the date, operation, mood, and character count
    And the receipt does not contain "Секретный текст дня."

  @di_open_010
  Scenario: DI-OPEN-010 — The Advisor directly opens an existing named day
    Given a Diary entry exists for "2026-08-14"
    When the Advisor opens that Diary entry
    Then the open request names that Diary entry directly
    And the Advisor does not need to route the open request

  @di_open_010
  Scenario: DI-OPEN-010 — The Advisor reports an absent current day without routing
    Given no Diary entry exists for "2026-08-15"
    When the Advisor looks up that Diary date
    Then no Diary entry is available to open
    And the Advisor does not need to route the open request
