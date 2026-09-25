Feature: Diary
  One entry per day, in the owner's own words. Safwa never writes one on its own — it proposes and
  the owner saves. Reading a day back, and pointing the owner at one, Safwa does itself.

  Background:
    Given a Diary with nothing in it yet

  Scenario: DI-DAY-001 — Writing a day that has nothing on it starts that day
    Given nothing is written for 15.08.2026
    When "Первый день." is written for that day, rated 8
    Then that day has one entry and only one
    And it holds those words, and that rating

  Scenario: DI-DAY-002 — Writing a day that already has something on it rewrites it whole
    Given 15.08.2026 reads "Уже записано.", rated 5
    When it is rewritten as "Переписал.", rated 7
    Then there is still one entry for that day
    And it holds the new words and the new rating
    And it is the same day rewritten, not a second one

  Scenario: DI-DATE-003 — A day put up for approval stays the day the owner named
    Given nothing is written for 14.08.2026
    When the Diary is asked to write "Запись за вчера." for that day
    Then what comes back for the owner to approve is aimed at 14.08.2026
    And nothing is in the Diary yet

  Scenario: DI-MOOD-004 — Rating a day is optional, and the scale is 0 to 10
    Given a day may be rated from 0 through 10, or not rated at all
    Then 0, 10 and no rating are all accepted
    But 11 is refused

  Scenario: DI-DELETE-005 — An approved deletion takes the day away
    Given 15.08.2026 reads "Есть что удалять.", rated 6
    When the owner approves deleting that day
    Then nothing is written for that day any more

  Scenario: DI-DELETE-005 — Deleting a day that was never written says so, and Safwa can carry on
    Given nothing is written for 15.08.2026
    When the Diary is asked to delete that day
    Then it answers that there is nothing there to delete
    And Safwa can act on that answer and try something else, rather than the turn ending there
    And nothing in the Diary changed

  Scenario: DI-READ-006 — Safwa reads a day back itself, and points the owner at it
    Given the owner asks what they wrote on a day
    When Safwa answers
    Then Safwa reads the Diary itself, without handing the question to anyone
    And it can point at that day in its answer
    And only writing to the Diary is handed over

  Scenario: DI-LINK-007 — What Safwa points at is the whole day, read-only
    Given 15.08.2026 reads "День с рынком.", rated 6
    When Safwa points at that day
    Then it is aimed at that day itself and no other
    And what the owner reads on it is the date, and the rating if there is one

  Scenario: DI-WRITE-008 — Safwa's own writing waits for the owner
    Given nothing is written for 15.08.2026
    When the Diary is asked to write "День до сохранения." for that day
    Then it comes back as something for the owner to approve, by PR-WRITE-002
    And nothing is in the Diary until they do

  Scenario: DI-RECEIPT-009 — The line the owner gets back never quotes the day itself
    When the owner saves or discards a day that reads "Секретный текст дня."
    Then the line they get back says the date, what was done, the rating, and how long the text was
    And the words of the day are not in that line, because the Diary is not something to quote back

  Scenario: DI-OPEN-010 — Safwa opens a day it was told the date of
    Given something is written for 14.08.2026
    When Safwa opens that day for the owner
    Then it opens that day directly, without handing the request to anyone

  Scenario: DI-OPEN-010 — Safwa says a day is empty rather than handing the question over
    Given nothing is written for 15.08.2026
    When Safwa looks that day up
    Then there is nothing to open
    And it still does not hand the request to anyone

  Scenario: DI-DAY-011 — A day with nothing written on it is never saved
    Given text that is empty, or only spaces
    When a day is written or rewritten with it
    Then it is refused
    And a day with nothing on it stays that way, and a day with words keeps the words it had

  Scenario: DI-DATE-012 — Today is the owner's today
    Given the owner is in Europe/Istanbul and it is just past midnight there
    When the Diary is told which day it is
    Then it is the owner's day, which at that hour is not the same date in UTC

  Scenario: DI-DATE-012 — A day nobody named is the owner's today
    Given the owner is in Europe/Istanbul and it is just past midnight there
    When a day is read without naming one
    Then it is the owner's day, from their midnight to the next

  Scenario: DI-READ-013 — A day is written from what was said that day and what is already written
    Given the Diary is asked to write a day
    When it reads that day
    Then it is handed that day's conversation, and the entry already saved for it if there is one
    And it is given nothing else to read

  Scenario: DI-MOOD-014 — Rewriting a day without naming a rating keeps the rating it had
    Given 15.08.2026 is rated 8
    When that day is rewritten and no rating is named
    Then the new words are saved and the day is still rated 8
    And a rewrite that names 3 rates it 3 instead

  Scenario: DI-READ-015 — A day nobody talked about reads as empty, not as a failure
    Given the owner said nothing to Safwa on a day
    When the Diary reads that day
    Then it comes back saying that day's conversation holds nothing
    And it does not fail, so the day can still be written from what the owner asks for

  Scenario: DI-READ-016 — Reading one named day reads the whole day
    Given the Diary is asked to read one day, named by its date
    When a Summary was written in the middle of that day
    Then the whole day is read as it was spoken, and the Summary is not read at all
    And the day runs from midnight to midnight in the owner's timezone
