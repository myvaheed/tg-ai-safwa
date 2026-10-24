Feature: Reminders
  A Reminder is some words and when they come due, and nothing else: a time, or a count of answers of
  Checks or Actions. Nobody builds a schedule by hand — not the owner and not Safwa. A time arrives as plain
  words, one short session turns those words into parameters, and the schedule is worked out from
  them in code; a count of answers arrives as its items and plain words, which a short session of
  its own reads. When one comes due,
  exactly one thing happens: the words are handed to Safwa as a
  request, and Safwa decides what to say.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace with the owner's own timezone, and a Safwa that proposes rather than writes

  Scenario: RM-SCHEDULE-001 — A time arrives as words and is worked out in code
    Given Safwa proposes a Reminder at a time and says it in plain words
    When the proposal is put together
    Then a short session turns those words into parameters
    And the schedule itself is worked out from those parameters in code
    And there is no box anywhere for a time, so words are the only way a time ever arrives; a count
      of answers arrives by RM-COND-030

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
    And a date with a time happens once, and the review screen and /reminders say it with its weekday, "once, on Thu 22.10.2026 at 10:00"
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
      (REMINDER_MIN_INTERVAL_MINUTES = 5), weekdays other than every day given together with an interval, a date with
      no hour on it, minutes from now given with anything else, a last day or a total on something
      that happens once, a last day before the first firing, days of the month given together with
      weekdays or an interval, days of the month with no hour, and nothing given at all
    And the short session of RM-SCHEDULE-001 is told the reason first, so the model can fix its
      parameters; when it cannot, the reason is what goes back, by RM-UNCLEAR-028

  Scenario: RM-CLOCK-006 — A firing time is a wall clock, not a fixed offset
    Given a daily Reminder at 08:30
    When the clocks go forward or back
    Then it still fires at 08:30
    And the edge of a Don't disturb window lands at the same clock time on both sides of the change

  Scenario: RM-QUIET-007 — Quiet hours push a firing to the end of them, and the end is not quiet
    Given Don't disturb in the Profile (PS-QUIET-025) and a Reminder of the owner's that repeats by an interval
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
    And a Reminder Remind made says how many minutes before each moment it fires in `lead_minutes`, by SCH-REMIND-028
    And no Reminder is in the block of context Safwa is given before every turn, because the next
      firing moves each time one goes off, and that block has to stay the same to stay cheap

  Scenario: RM-UNCLEAR-028 — Words that cannot be scheduled come back with what is unclear and the forms that work
    Given Safwa proposes a Reminder whose words do not say when, or say a time no schedule can keep
    When those words are worked through
    Then nothing is written, and Safwa is told what is unclear and the forms a time may take:
      "tomorrow at 15:00", "on Thursday at 10:00", "every weekday at 8:00", "every 2 hours"
      (REMINDER_TIME_FORMS), as it is when the short session could not read them at all
    And words that count answers of Checks or Actions go back to Safwa without forms, to name them
      as a condition by RM-COND-030
    And Safwa tells the owner, and tries again with the owner's answer, by RM-SCHEDULE-002

  Scenario: RM-COND-030 — A Reminder can come due on answers of Checks or Actions
    Given the owner asks for a word at every 3 Missed in a row of Check #12
    When Safwa proposes the Reminder
    Then it names the items and passes the owner's words, and a short session reads from the words
      the answer, how many, whether in a row and whether once; the items do not pass through it
    And the items are Checks or Actions by #id, such as "Check #12", "Check #12 «Sat straight?»" or
      "Action #3, Action #5", and code keeps the series of each, by the series id that stays when any
      copy is deleted
    And a group in words, such as "Actions tagged sport", is listed by #id by Safwa before it
      proposes the Reminder, and words that name no #id go back to Safwa by RM-COND-031
    And the answer is Passed, Missed, any answer of a Check, or Done of an Action, and only Passed and
      Missed count in a row
    And the short session names the answer only as yes, no or any; whether the items are Checks or
      Actions comes from their words
    And words that do not say how many, such as "a decent run", go back to Safwa to ask the owner
    And an #id with neither Check nor Action beside it, and Missed of Actions, go back to Safwa
    And the screen says "every 3 Missed in a row of Check #12", the items it counts by up to 3 titles
      and how many more (CONDITION_PREVIEW_TITLES = 3), then their count now, such as "0 Missed in a
      row now", and shows no first firing
    And Save makes a Reminder with no time: the clock never fires it, and a restart does not move it

  Scenario: RM-COND-031 — The items are checked before the owner sees them
    Given Safwa names the items
    When they are words other than Checks or Actions by #id, Checks and Actions together, or an #id
      that does not exist
    Then nothing is written, and Safwa is told what is wrong, to find each item's #id and try again

  Scenario: RM-COND-032 — A Reminder on answers fires at every multiple of its count
    Given a Reminder on 3 Missed in a row of a repeating Check
    When an answer to a copy of one of its series is saved — by hand, or from a proposal the owner saved
    Then the answers of every copy of its series are counted, newest first, and a Pending instance is
      skipped
    And in a row counts back to the first other answer, and in all counts every such answer
    And at 3, 6 and 9 its words go to Safwa as one request, as any Reminder's do when it comes due,
      saying how many there are now and how many Passed and Missed in all
    And a fourth and a fifth Missed say nothing, and neither does an answer it does not count
    And answers saved together, such as the Checks one Done answers, count one by one in the order
      saved, each with the count at its own place: 3 or 4 of them under every 3 answers say it once,
      at the third
    And a corrected answer says nothing, even when the count it leaves is a multiple
    And Done of an Action counts the same way, in all, and says how many are Done now
    And it stays after it fires, and deleting it is the only off switch, by RM-WRITE-010; it is not in
      Profile → Hooks

  Scenario: RM-COND-033 — The series are fixed when the Reminder is set
    Given a Reminder on "every 10 Done of Action #3 and Action #5"
    When a new copy of Action #3 is Done
    Then it counts, as every copy of its series does, answered or open
    And so a deleted first copy still leaves every answer of that series counted
    When an Action made after the Reminder is Done
    Then it does not count, even with the same Tag as Action #3
    And deleting every copy of its series leaves the Reminder, which counts nothing and says so on its
      screen

  Scenario: RM-COND-034 — A count one series can never reach is refused
    Given items that are one series of a Check or of an Action
    When the most it can count is N: what it counts now, and every answer the series still has to
      come, one while it does not repeat and is open, or what is left of its total by SCH-TOTAL-021
    Then a count above N is refused, saying that the series does not repeat, or how many it counts
      now and how many answers it has left, and Safwa tells the owner
    And 2 Passed under an earlier revision, with a new revision of 2 times, reach 4 Passed in all
    And a Check runs as its own Schedule does, or else as the Action it hangs on
    And items of more than one series, or a series that repeats without end, take any count

  Scenario: RM-COND-035 — A Reminder on answers is listed and read like any other
    Given a Reminder on answers, and one with a time
    When the owner opens /reminders
    Then the one with a time comes first, and the one on answers follows, by its words and its answers
    And opening it shows its answers and no next firing, and the same two things to do, by RM-UI-023
    And it shows the items it counts now, or that its items were deleted
    And Safwa reads it in ai_reminders with schedule_kind condition and its condition: the items,
      their series, the answer, the count, whether in a row and whether once, and no next firing

  Scenario: RM-END-036 — A repeating Reminder can end on a last day or after a total
    Given Safwa proposes a repeating Reminder whose words say until when, for how long, or how many times
    When the short session of RM-SCHEDULE-001 reads them
    Then the last day comes as a date, a weekday or how long, and the total as a number, and the
      last day is worked out in code
    And how long counts from the start date, or from today without one: 2 weeks from 17.10.2026
      runs through 30.10.2026, and a month from 31.01.2027 through 28.02.2027
    And a weekday is the next such day on or after the start, and a date without a year the next
      such date
    And a one-time date without a year is the next such date whose time is still ahead
    And a repeat's start date without a year is the one at most 183 days behind or ahead of
      today (NEAREST_START_DAYS = 183): from 15.10 on 17.10.2026 it has begun on 15.10.2026 and
      fires from the next time, and from 10.01 on 28.12.2026 it begins on 10.01.2027
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

  Scenario: RM-MONTH-037 — A Reminder can fire on days of the month
    Given Safwa proposes a Reminder whose words name days of the month and a time, such as "on the 1st
      and 15th of each month at 9"
    When the short session of RM-SCHEDULE-001 reads them
    Then the days come as numbers from 1 to 31, and the dates they fall on are worked out in code
    And 31 is the last day of every month, and a day a month lacks is that month's last day: the 30th
      fires on 28.02.2027
    And the review screen and /reminders say it, "on the 1st and 15th of each month at 09:00", and the
      31st as "on the last day of each month at 18:00"
    And it keeps its local clock when the clocks go forward or back, by RM-CLOCK-006, and startup
      works it out again when its stored moment no longer lands on its day and time, by RM-START-020
    And it can start on a date and end on a last day or after a total, by RM-END-036
    And "once a month" with no day and no time does not pin down a time, by RM-SCHEDULE-002
    And Safwa reads it in ai_reminders with schedule_kind monthly and its month_days

  Scenario: RM-EDIT-038 — A Reminder proposal's words are edited as the Reminder's own Text asks
    Given Safwa proposes to make a Reminder, or to change one's words
    When the owner presses "✏️ Edit" on its review screen
    Then the words have "✏️ Text", which asks for them as the Reminder's own "✏️ Text" does
    And when the proposal sets a time, that is read again with the new words before the review
      comes back, as it is for Safwa's; a count of answers is not read from the words
    And a time that cannot be read with them leaves the proposal as it was, with what is unclear
      on the screen
    And when it fires has no button

  Scenario: RM-TRACK-039 — A new Reminder at a time of day can also be tracked as a Check
    Given Safwa proposes a Reminder "Remind the user to drink water" every day at 07:00, and the owner has the Values "Health" and "Family"
    When its review screen is drawn
    Then "📊 Also track" stands above Save and Discard, by PR-SWITCH-037
    And a Reminder that happens once, repeats by an interval or counts answers has no such button, and neither has a change to a Reminder
    When the owner presses it
    Then a short session writes a yes/no question in the language of the words, "Drank water?", and picks the Values the habit plainly serves, "Health"
    And a Value name not in the list is sent back to the session to fix, and none at all is fine
    And the review now proposes a new Check "Drank water?" with the Schedule "every day at 07:00", Remind at the time and the Value "Health"
    And the Schedule is the Reminder's own timing, carried over in code and not read again: its last day and its total are the Schedule's, and the screen says the total
    And Edit offers the Check's own fields, "💎 Values" among them, by CH-EDIT-022
    When the owner saves it
    Then the Check, its Schedule, the Reminder Remind makes and the link to "Health" are written together, and no other Reminder is
    And the link gets the small check of VL-LINK-019, as any saved link of a Value to a Check does
    When the owner discards it
    Then nothing is written

  Scenario: RM-COND-040 — A Reminder after N answers fires once
    Given the owner asks for a word after 30 Passed in a row of Check #7
    When Safwa proposes the Reminder
    Then the short session reads "after 30" as once, and "every 30" as at every multiple
    And the screen says "after 30 Passed in a row of Check #7", and what it counts now, by RM-COND-030
    When an answer brings the count to 30 itself
    Then its words go to Safwa as one request that says it is its last time, and the Reminder is
      deleted as that request is written
    And no answer after it says anything
    And answers saved together say it once, at the 30th, by RM-COND-032
    When the count is already 30 or more as the Reminder is proposed
    Then it is refused, saying the count it has now and that after 30 is already past, and Safwa
      tells the owner
    And every 30 is not refused: it fires at the next multiple

  Scenario: RM-AGENT-041 — Reminders and Remind are set by their own part of Safwa
    Given a request to set, change or delete a Reminder, at a time or on answers, or to turn Remind
      on or off for an Action, a Goal or a Check
    Then it is handed to the reminder part, and the workspace part has no tool for a Reminder or
      Remind
    And the reminder part reads the Reminders, Checks, Cards, Tags and Values, and is not handed the
      state of the workspace
    And it is handed the open Checks, Actions and Goals that have a Schedule, each by #id with its
      Schedule, Tags and Values, up to 50 (OPEN_ITEMS_MAX = 50), and finds any other with search
    And it proposes a Reminder by RM-WRITE-008 and Remind by SCH-REMIND-029, and deletes a
      Reminder with the same tool that proposes one
    And it has no tool that creates, archives or deletes a Check or an Action
    When it would change or delete the Reminder an item's Remind made
    Then it is refused with that item's id, to set the item's Remind instead
    When the request also asks for a new item, such as an Action every day at 20:00 with Remind 30
      minutes before
    Then the workspace part proposes the item, and after its Save the reminder part proposes its
      Remind
