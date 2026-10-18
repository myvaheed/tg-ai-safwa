Feature: Reminders
  A Reminder is some words and when they come due, and nothing else: a time, or a count of answers of
  Checks or Actions. Nobody builds a schedule by hand — not the owner and not Safwa. The timing arrives as plain
  words, one short session turns those words into parameters, and the schedule is worked out from
  them in code. When one comes due, exactly one thing happens: the words are handed to Safwa as a
  request, and Safwa decides what to say.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace with the owner's own timezone, and a Safwa that proposes rather than writes

  Scenario: RM-SCHEDULE-001 — Timing arrives as words and is worked out in code
    Given Safwa proposes a Reminder and says when in plain words
    When the proposal is put together
    Then a short session turns those words into parameters
    And the schedule itself is worked out from those parameters in code
    And there is no box anywhere for a schedule, so words are the only way timing ever arrives

  Scenario: RM-SCHEDULE-002 — Words that do not pin down a time ask the owner
    Given the owner says "remind me every morning"
    When those words are worked through
    Then the answer is that they do not pin down a time, together with the one question to ask
    And that question goes back to Safwa, which can ask it and try again
    And nothing was written: no Reminder, and nothing waiting for the owner

  Scenario: RM-SCHEDULE-003 — A date is always a start; a time is a firing clock only next to weekdays
    Given the words have been turned into parameters
    When the schedule is worked out
    Then an interval on its own starts now and repeats
    And an interval with a time starts the next time that clock comes round
    And an interval with a date and a time starts at that exact moment
    And weekdays with a time fire weekly at that clock, or daily when all seven are given
    And weekdays with a date fire on the first of those days on or after it
    And a date with a time happens once
    And a time on its own is the next time that clock comes round
    And minutes from now happen once, at that minute, counted from now in code
    And a weekday given as the date is the next such day whose time is still ahead, today included
    And today or tomorrow given as the date is that day, counted in code
    And every day given together with an interval is the interval alone

  Scenario: RM-SCHEDULE-004 — A repeat that started in the past is simply already running
    Given a repeating schedule that started before now
    When its first firing is worked out
    Then it is the next one from now
    And the ones that would have happened before now are not made up

  Scenario: RM-SCHEDULE-004 — A one-off in the past cannot be honoured
    Given something that happens once, at a moment that has already gone
    When the schedule is worked out
    Then it is refused, because that moment cannot be honoured

  Scenario: RM-SCHEDULE-005 — Parameters that cannot make a schedule are named, never corrected
    Given parameters that contradict each other, or fall outside what is allowed
    When the schedule is worked out
    Then the reason is said plainly and no schedule is made
    And each of these is such a reason: an interval under 5 minutes
      (REMINDER_MIN_INTERVAL_MINUTES = 5), weekdays other than every day given together with an interval, quiet hours on
      something that is not an interval, quiet hours that leave no time of day at all, a date with
      no hour on it, minutes from now given with anything else, a last day or a total on something
      that happens once, a last day before the first firing, and nothing given at all
    And the short session of RM-SCHEDULE-001 is told the reason first, so the model can fix its
      parameters; when it cannot, the reason is what goes back, by RM-UNCLEAR-028

  Scenario: RM-CLOCK-006 — A firing time is a wall clock, not a fixed offset
    Given a daily Reminder at 08:30
    When the clocks go forward or back
    Then it still fires at 08:30
    And the edge of a quiet window lands at the same clock time on both sides of the change

  Scenario: RM-QUIET-007 — Quiet hours push a firing to the end of them, and the end is not quiet
    Given a repeating Reminder with quiet hours
    When a firing would land inside them
    Then it moves to the moment they end
    And one range written across midnight behaves the same as two ranges meeting at 00:00
    And when quiet hours run into each other, a firing is pushed through all of them

  Scenario: RM-WRITE-008 — No Reminder is written without the owner seeing it
    Given Safwa proposes a Reminder, by PR-WRITE-002
    When the owner reads the screen
    Then it says the time that was worked out, not the words that were said
    And it shows the Reminder's words, when they fire and when they fire first, and never the stored parameters
    And an edit shows each change as before → after, and a deletion shows what is deleted
    And Save makes the Reminder with exactly that schedule and that first firing
    And Discard leaves no Reminder and no trace of one

  Scenario: RM-WRITE-009 — Changing the words leaves the time alone
    Given Safwa is editing a Reminder
    When it sends new words and says nothing about time
    Then nothing works out a new schedule, and the Reminder keeps the one it had

  Scenario: RM-WRITE-009 — Changing the words by hand leaves the time alone
    Given the owner opens a Reminder and edits its text
    When they send the new text
    Then the Reminder still fires when it was going to
    And empty text is refused, and the Reminder comes back on screen unchanged

  Scenario: RM-WRITE-010 — Deleting is the only off switch
    Given a Reminder
    When it is removed, by the owner from its screen or by Safwa with the owner's approval
    Then it is gone and it stops firing at once
    And there is nowhere it went and no switch to turn it back on
    And removing it is one confirmation, not the are-you-sure a Card gets
    And Remind Off on a Card's or Check's Schedule, by SCH-REMIND-019, removes its Reminder the same way

  Scenario: RM-FIRE-011 — A Reminder that comes due hands its words to Safwa
    Given a Reminder comes due
    When it goes off
    Then Safwa is given the words and the schedule as one request
    And Safwa reads the same conversation it would read for anything the owner said
    And what it says goes into the chat as something Safwa said unasked, by TG-KIND-002
    And nothing in between decides what the Reminder meant — that is Safwa's job

  Scenario: RM-FIRE-012 — One check is one turn
    Given 5 Reminders come due at the same moment
    When they are checked
    Then the 3 oldest go to Safwa as one request (REMINDER_FIRE_BATCH = 3)
    And the other 2 stay due, and go once those 3 have been said

  Scenario: RM-FIRE-013 — A Reminder's words are written down before anything moves on
    Given a Reminder is due
    When it goes off
    Then its words are written down first, and only then does it move on to its next time
    When Safwa is busy, or the turn fails, or the owner speaks in the middle of it
    Then those words are still waiting, and the next check says them, 30 seconds later
      (SCHEDULER_POLL_SECONDS = 30)
    And once they have reached the chat they are never said again, by AG-CUE-029

  Scenario: RM-FIRE-014 — A Reminder firing is not a change the owner made
    Given a Reminder went off and moved on
    When the workspace's change count is read
    Then it has not moved, so a firing never invalidates a screen the owner is looking at, or an
      answer already on its way

  Scenario: RM-FIRE-015 — A repeat counts from when it was due, not from when it was picked up
    Given a repeating Reminder due at 08:30, picked up four minutes late
    When its words are written down
    Then the next firing is worked out from 08:30
    And a late check does not drag every later firing later with it

  Scenario: RM-FIRE-016 — A one-off fires exactly once, however late
    Given a one-off Reminder that is overdue
    When it is picked up
    Then it fires, and the request says how late it is
    And it removes itself the moment its words are written down
    And it never goes off a second time

  Scenario: RM-GATE-017 — A Reminder that comes due while Reminders are already waiting stays due
    Given a Reminder's words are waiting to be said, and Safwa is not free to say them, by AG-TURN-015
    When another Reminder comes due
    Then it writes nothing and stays due, so the words waiting are still the only ones waiting
    And an hour of that is one message when Safwa frees up, not twelve

  Scenario: RM-CATCHUP-019 — A repeat missed by a little still fires, once
    Given a repeating Reminder that is 90 minutes overdue
    When it is picked up
    Then it fires once, and that one firing is the whole catching-up
      (90 is under REMINDER_CATCHUP_GRACE_MINUTES = 120)

  Scenario: RM-CATCHUP-019 — A repeat missed by a lot moves on quietly
    Given a repeating Reminder that is 3 hours overdue
    When it is picked up
    Then it moves on to its next time and the owner is told nothing
    And however often it repeats: one every 5 minutes, offline for 3 hours, says nothing at all
      rather than 36 times

  Scenario: RM-START-020 — Startup fixes what being down made wrong, and only that
    Given Safwa starts
    When the Reminders are looked over
    Then a Reminder set to a time of day, whose stored moment no longer lands on that time, is
      worked out again
    And a repeat overdue by more than the grace moves on quietly
    And a repeat overdue by less is left for the first check to fire
    And a one-off is never moved

  Scenario: RM-POLL-021 — Reminders do not stop firing because one check fell over
    Given one check of the due Reminders raises
    When the next one comes round 30 seconds later (SCHEDULER_POLL_SECONDS = 30)
    Then it runs, by AG-POLL-030
    And Reminders do not quietly stop firing for the rest of the day

  Scenario: RM-UI-023 — /reminders is a list, a Reminder, and two things to do with it
    Given the owner opens /reminders
    Then each line is what it is about, then when it fires, soonest first
    And what it is about is the title of the Card or Check Remind made it for, or else the start of its words
    And one Remind made does not say when it starts, which is its item's Schedule
    And opening one shows when it fires next and all of its words
    And the only things to do are change the words and delete it
    And there is no way to make one here, and no way to change a time here
    And an empty list says that Safwa is who makes them

  Scenario: RM-READ-024 — Safwa looks Reminders up, rather than being told them every turn
    Given Reminders exist
    When Safwa needs to know about them
    Then it looks them up, and gets the schedule exactly as it is stored
    And the next firing reads in the owner's own clock, not as the moment it is stored as
    And no Reminder is in the block of context Safwa is given before every turn, because the next
      firing moves each time one goes off, and that block has to stay the same to stay cheap

  Scenario: RM-UNCLEAR-028 — Words that cannot be scheduled come back with what is unclear and the forms that work
    Given Safwa proposes a Reminder whose words do not say when, or say a time no schedule can keep
    When those words are worked through
    Then nothing is written, and Safwa is told what is unclear and the forms a Reminder's timing may take
    And the words meant a time, so the forms are times: "tomorrow at 15:00", "on Thursday at 10:00",
      "every weekday at 8:00", "every 2 hours except 22:00–09:00" (REMINDER_TIME_FORMS)
    And words that meant answers get the answer forms: "every 3 Missed in a row of Check #12",
      "every 30 answers of Check #12", "every 10 Done of Actions tagged sport" (REMINDER_ANSWER_FORMS)
    And words where neither can be told, or that the short session could not read at all, get both
    And Safwa tells the owner, and tries again with the owner's answer, by RM-SCHEDULE-002

  Scenario: RM-COND-030 — A Reminder can come due on answers of Checks or Actions
    Given Safwa proposes a Reminder with the words "every 3 Missed in a row of Check #12"
    When those words are worked through
    Then the short session of RM-SCHEDULE-001 reads which items, which answer, how many, and whether in a row
    And a second short session writes those items as one SELECT of their ids, over ai_checks for a
      Check's answer and over ai_cards for Done
    And that session is told to find the items whatever their answer, never filtering on status or
      stage, so the answers are counted in code
    And that session is given every Tag and Value by the name stored, so "tagged sport" finds a Tag
      named Sport
    And the answer is Passed, Missed, any answer of a Check, or Done of an Action, and only Passed and
      Missed count in a row
    And the screen says "every 3 Missed in a row of Check #12", and what the SELECT finds now by up to
      3 titles and how many more (CONDITION_PREVIEW_TITLES = 3), and shows no first firing
    And Save makes a Reminder with no time: the clock never fires it, and a restart does not move it

  Scenario: RM-COND-031 — The SELECT is checked before the owner sees it
    Given the second short session writes the SELECT
    When it is not one SELECT over ai_checks and ai_cards, or a Check's answer does not read ai_checks,
      or Done does not read ai_cards
    Then it goes back to that session to repair, by its repair rounds (MINI_SESSION_REPAIR_ROUNDS = 3)
    And a SELECT that still fails, does not run, or returns no id column comes back as words that
      cannot be scheduled, with the answer forms of RM-UNCLEAR-028

  Scenario: RM-COND-032 — A Reminder on answers fires at every multiple of its count
    Given a Reminder on 3 Missed in a row of a repeating Check
    When an answer to an item its SELECT finds is saved — by hand, or from a proposal the owner saved
    Then the answers of every item the SELECT finds now are counted, newest first, and a Pending
      instance is skipped
    And in a row counts back to the first other answer, and in all counts every such answer
    And at 3, 6 and 9 its words go to Safwa as one request, as any Reminder's do when it comes due,
      saying how many there are now and how many Passed and Missed in all
    And a fourth and a fifth Missed say nothing, and neither does an answer it does not count
    And a corrected answer says nothing, even when the count it leaves is a multiple
    And Done of an Action counts the same way, in all, and says how many are Done now
    And it stays after it fires, and deleting it is the only off switch, by RM-WRITE-010; it is not in
      Profile → Hooks

  Scenario: RM-COND-033 — The SELECT runs again at every answer
    Given a Reminder on "every 10 Done of Actions tagged sport"
    When an Action tagged sport after the Reminder was made is Done
    Then it counts
    And deleting every item the SELECT finds leaves the Reminder, which counts nothing until its SELECT
      finds something again

  Scenario: RM-COND-034 — A count one series can never reach is refused
    Given words whose SELECT finds one series of a Check or of an Action
    When that series runs at most N times: once when it does not repeat, or its total by SCH-TOTAL-021
    Then a count above N is refused with the answer forms of RM-UNCLEAR-028
    And a Check runs as its own Schedule does, or else as the Action it hangs on
    And a SELECT that finds more than one series, or a series that repeats without end, takes any count

  Scenario: RM-COND-035 — A Reminder on answers is listed and read like any other
    Given a Reminder on answers, and one with a time
    When the owner opens /reminders
    Then the one with a time comes first, and the one on answers follows, by its words and its answers
    And opening it shows its answers and no next firing, and the same two things to do, by RM-UI-023
    And it shows what its SELECT finds now, or, when the SELECT no longer runs, that it counts
      nothing, why, and to ask Safwa to set it again
    And Safwa reads it in ai_reminders with schedule_kind condition and its condition: the items, the
      SELECT, the answer, the count and whether in a row, and no next firing

  Scenario: RM-END-036 — A repeating Reminder can end on a last day or after a total
    Given Safwa proposes a repeating Reminder whose words say until when, for how long, or how many times
    When the short session of RM-SCHEDULE-001 reads them
    Then the last day comes as a date, a weekday or how long, and the total as a number, and the
      last day is worked out in code
    And how long counts from the start date, or from today without one: 2 weeks from 17.10.2026
      runs through 30.10.2026, and a month from 31.01.2027 through 28.02.2027
    And a weekday is the next such day on or after the start, and a date without a year the next
      such date
    And a start date without a year is the next such date whose time is still ahead
    And the review screen and /reminders say it, "every day at 09:00, until 30.11.2026, 10 times"
    And it does not fire after its last day, nor more times than its total
    And the request of its last firing says "This is its last time." (LAST_FIRE), and it deletes
      itself once those words are written down
    And one that moves on quietly past its last day, by RM-CATCHUP-019 or RM-START-020, deletes
      itself and says nothing
    And opening one with a total shows how many have fired, "3 of 10 fired."
    And a new schedule counts its total from 0
    And a last day or a total on a one-off, or a last day before the first firing, is refused, by
      RM-SCHEDULE-005
