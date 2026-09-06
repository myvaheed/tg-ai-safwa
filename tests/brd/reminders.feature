Feature: Reminders
  A Reminder is some words and a time, and nothing else. Nobody builds a schedule by hand — not the
  owner and not Safwa. The timing arrives as plain words, one short session turns those words into
  parameters, and the schedule is worked out from them in code. When one comes due, exactly one thing
  happens: the words are handed to Safwa as a request, and Safwa decides what to say.

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
      (REMINDER_MIN_INTERVAL_MINUTES = 5), weekdays given together with an interval, quiet hours on
      something that is not an interval, quiet hours that leave no time of day at all, a date with
      no hour on it, and nothing given at all

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
    And removing it is one confirmation, not the are-you-sure a whole Card tree gets

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

  Scenario: RM-GATE-017 — A Reminder that comes due while words are already waiting stays due
    Given words are waiting to be said, and Safwa is not free to say them, by AG-TURN-015
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

  Scenario: RM-POLL-021 — The checking outlives its own failures
    Given one check raises
    When the next one comes round 30 seconds later (SCHEDULER_POLL_SECONDS = 30)
    Then it runs, and the failure was written down rather than lost
    And Reminders do not quietly stop firing for the rest of the day

  Scenario: RM-SYSTEM-022 — A Reminder the owner did not set belongs to Safwa
    Given a Reminder Safwa set up rather than the owner — the Diary nudge, or a Sprint's own warning
      that it is ending
    When the owner opens /reminders, or Safwa looks at the Reminders
    Then it is not in the list
    And every way of editing, rescheduling or deleting it refuses, and says where to change it
    And it fires exactly like any other Reminder
    And whatever set it up is what takes it away: the Profile for the Diary nudge, finishing the Sprint
      for its ending warnings

  Scenario: RM-UI-023 — /reminders is a list, a Reminder, and two things to do with it
    Given the owner opens /reminders
    Then each line is when it fires and the start of its words, soonest first
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
