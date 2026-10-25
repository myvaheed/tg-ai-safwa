Feature: One plain-language Schedule for Actions and Checks

  Scenario: SCH-QUOTA-001 — A calendar quota opens one current instance
    Given an Action scheduled five times a day
    When the fifth instance is completed
    Then one next instance belongs to the following local day
    And the plan reports five completed instances in the original day

  Scenario: SCH-WINDOW-002 — A week covered in part plans its share of the quota
    Given a Check scheduled three times a week without a clock
    When the Advisor queries Wednesday through Sunday of one week, and then Monday and Tuesday of it
    Then 2 answers are planned for the five days and 1 for the two: the quota times the days covered, divided by 7, rounded
    And a month covered in part plans its share by the days of that month: 3 a month over 10 of November's 30 days plans 1
    And no appointment time is invented

  Scenario: SCH-CHECK-003 — A scheduled Check is an observation series
    Given a Check with its own Schedule
    When it is answered Missed
    Then the answer counts as an observation and the next instance is opened
    And the Check can be attached to a Goal, never to an Action

  Scenario: SCH-COMPILE-004 — A Schedule proposed in words is read before it is saved
    Given a Schedule proposed in words on an Action or a Check
    When the proposal is prepared
    Then the Scheduler reads it before the review screen opens, and the screen shows how it was read
    And Save writes the Schedule with that reading, so the Action or Check can be finished at once
    And a changed Schedule starts a new revision, while earlier answers keep the revision they were made under

  Scenario: SCH-CLARIFY-005 — An unclear Schedule is asked about in the same reply
    Given a Schedule proposed in words that names no timing
    When the Scheduler asks a question
    Then no proposal is made, and the question goes back to the subagent to ask the owner
    And nothing is saved, and no later message asks it again

  Scenario: SCH-ZONE-006 — Periods use the workspace calendar
    Given a workspace timezone with daylight saving time
    When a weekly period is calculated
    Then its boundary is local Monday midnight rather than UTC midnight

  Scenario: SCH-DELETE-007 — Deleting the current instance ends its Schedule
    Given a repeating Action or Check with completed observations
    When the owner deletes its current open instance
    Then no future work is reported for that series
    And its retained observations still count in history

  Scenario: SCH-END-008 — A final Action can be reopened after repetition ends
    Given a repeating Action whose current Schedule is cleared or changed to one appointment
    When the final Action is completed and then reopened
    Then it can be used as an ordinary Action
    And earlier completed instances stay closed

  Scenario: SCH-INTERVAL-009 — Early completion counts for the period it happens in
    Given an Action or Check repeating at a fixed interval
    When its next appointment is completed before it comes
    Then the completion counts for the period it happens in, by SCH-PERIOD-031
    And the next appointment stays the one that was next

  Scenario: SCH-REPAIR-010 — Clear timing does not ask the owner to fix model formatting
    Given a Schedule with an explicit weekday and clock
    When the model supplies the clock in an invalid format
    Then the Scheduler asks the model to repair its arguments
    And a valid repair configures the Schedule without a question to the owner

  Scenario: SCH-READ-011 — A malformed schedule query can be corrected in the same turn
    Given the Advisor asks for scheduled work with invalid dates or type
    When the read request is rejected
    Then the Advisor receives a precise correction instruction
    And can retry and answer in the same turn

  Scenario: SCH-SUMMARY-012 — One series has one compact progress summary
    Given scheduled Actions or Checks with observations before and during the current Sprint
    When the Advisor reads their plan
    Then each series appears once with its next event and selected-range, Sprint and lifetime progress
    And Passed and Missed remain distinct within answered Checks
    And an unlimited series has no invented lifetime remainder
    And large results can be continued without losing a series

  Scenario: SCH-STAGE-013 — A generated Action opens where its next slot falls
    Given an Action with a compiled Schedule is finished and its next copy opens
    When a Sprint is running
    Then a copy due today opens in Today, and one due later by the Sprint's planned last day opens in Sprint
    And a weekly or monthly quota's copy opens in Sprint while its week or month starts by the Sprint's last day
    And a copy due after the Sprint's last day opens in Backlog
    And an after-completion copy keeps Today or Sprint, and one from Backlog opens in Sprint
    When no Sprint is running
    Then a copy from Backlog stays in Backlog
    And a copy from Sprint or Today opens in Today when due today, keeps its stage when it repeats after completion, and otherwise opens in Sprint

  Scenario: SCH-RETRY-014 — A Schedule the model cannot read asks for other words
    Given the Scheduler's model gives no valid reading within its tool calls
    Then the owner is asked to say the Schedule in other words, like one of the forms it may take:
      "three times a week", "every Monday and Thursday at 9:00", "on 20 October at 15:00",
      "every day until 30 November", "after I finish it" (SCHEDULE_FORMS)
    And a Deadline names its own: "by 20 October", "by 20 October at 18:00", "end of next month" (DEADLINE_FORMS)
    And nothing is saved

  Scenario: SCH-LIMIT-015 — An Action repeats at most five times a day
    Given a Schedule on an Action that plans more than 5 executions a day (ACTION_DAILY_EXECUTIONS_MAX = 5)
    Then the Scheduler asks whether to make it a Check instead or choose fewer, rather than saving it
    And a daily quota of 6, a weekly quota of 36, a monthly quota of 151, a month counted as 30 days,
      and an interval under 288 minutes are each asked about
    And a Check with the same Schedule is saved

  Scenario: SCH-EDITOR-016 — A Schedule typed into an editor is read before it is saved
    Given the owner types a Schedule on an Action's, a Check's or a Goal's screen, or into a Card draft
    Then the Scheduler reads it before anything is written, and the screen shows how it was read
    And a question from the Scheduler is shown on the same editor, which keeps waiting, and nothing is saved
    And Safwa shows that it is typing while the Scheduler reads it
    And off clears the Schedule without asking the model
    And a draft saved after its Schedule was read creates the Card with that Schedule ready

  Scenario: SCH-CLOCK-017 — A clock alone never makes a one-time appointment
    Given the Schedule "every evening at 20:00"
    When the model supplies the time without days or a date
    Then the Scheduler asks the model to add days to repeat it, or a date for one time
    And a repair with every weekday and 20:00 repeats every day at 20:00
    And a clock with a date, such as "this Wednesday at 15:00", stays one appointment at that moment
    And minutes from now, such as "in 30 minutes", are one appointment at that minute, counted in code

  Scenario: SCH-DAY-018 — A Schedule without a clock is accepted
    Given a Schedule such as "every evening", "every Monday" or "20 October"
    Then the Scheduler is told that a part of the day is not a time, and to read "every evening" as once a day
    And weekdays or a date without a clock become an appointment for that whole day, due by 23:59 (END_OF_DAY)
    And the screens show its day without a clock, and it is overdue only after that day ends
    And an all-day appointment set during its day is planned for that day

  Scenario: SCH-REMIND-019 — A Schedule with a clock offers Remind beside Edit
    Given an Action, a Goal or a Subgoal, or a Check whose Schedule or Deadline has a clock still ahead
    When the owner opens its Schedule
    Then the screen shows how it was read, with "🔔 Remind: Off", "✏️ Edit" and "↩️ Back"
    And Remind opens its choices: "Off", "At the time", "15 minutes before", "1 hour before", "1 day before",
      "✏️ Other" and "↩️ Back", the one in force marked "✅"
    And a choice makes a Reminder that many minutes before each of the Schedule's moments, by SCH-REMIND-028,
      with the words "Remind is on for Action #12 «Stretch». Remind the owner about it." (REMIND_TEXT),
      and the Schedule comes back with "🔔 Remind: 15 minutes before"
    And a choice on a Reminder already made changes only how long before it fires
    And Other takes the minutes typed, 0 to 1440 (REMIND_LEAD_MAX_MINUTES), and refuses any other value on the same editor
    And Off deletes that Reminder
    And a quota, a whole-day appointment, repetition after completion, a Deadline without a time, or a one-time moment already past opens the editor at once

  Scenario: SCH-REMIND-020 — Remind's Reminder follows its item
    Given Remind is on for a repeating Action or Check
    When it is finished before its appointment
    Then the Reminder names the next instance and fires first at that instance's appointment
    And an instance left open moves on to each new period, by SCH-PERIOD-031, and is reminded at
      the Schedule's next moment, and at each one after it; moving on leaves the Reminder as it is
    And a changed Schedule moves the Reminder to it, and a Schedule without a clock deletes it
    And the Reminder keeps how long before each moment it fires
    And a renamed item rewrites the Reminder's words
    And finishing a one-time Action, closing a Goal, or deleting the item deletes the Reminder
    And closing a Goal deletes the Reminder of a scheduled Check on it
    And deleting the Reminder in /reminders turns Remind off

  Scenario: SCH-TOTAL-021 — A repeating Schedule can stop after a total
    Given the Schedule "five times a day, 50 times in total" on an Action or a Check
    Then the Scheduler reads it as 5 a day with a total of 50, and the screen shows "5 per day, 50 times in total."
    And the 50th instance answered under that Schedule opens no next one: Done counts on an Action, Passed and Missed on a Check
    And the Schedule's screen of an instance says how many of the total are answered, "12/50 answered so far."
    And a changed Schedule counts its total from 0, as a new revision by SCH-COMPILE-004
    And a total on a one-time Schedule goes back to the Scheduler's model to repair, and a Deadline has none
    And planning counts no more of the work than the total has left, and the plan's lifetime remainder is what the total has left

  Scenario: SCH-TOTAL-022 — Safwa says when a Schedule has run its total or reached its last day
    Given an Action or a Check whose Schedule has a total, or a last day by SCH-END-025
    When its last instance is answered
    Then Safwa is asked to tell the owner, naming it with its total or its last day, that its Schedule ran all of its times or reached its last day and it no longer repeats, and to ask whether to start it again
    And nothing is changed until the owner answers
    And the request tells Safwa to start it again by proposing its Schedule on that item
    And switching Schedule end off in Profile → Hooks stops the request and drops what is pending

  Scenario: SCH-TOTAL-023 — A Schedule on the last instance of an ended series starts it again
    Given an Action or a Check whose Schedule ran its total or reached its last day, so nothing in its series is open
    When a proposal to write a Schedule on its last instance is saved
    Then the next instance of the series opens with that Schedule, and every field written goes to it
    And its total counts from 0, even in the same words, as a new revision by SCH-COMPILE-004
    And the last instance stays closed under the revision it was answered under
    And an instance older than the newest of its series still refuses a Schedule

  Scenario: SCH-FORM-024 — Once a day beside weekdays or a clock is read as those days
    Given the model reads "every evening at 20:00" as once a day together with every weekday and 20:00
    Then the Scheduler reads it as every day at 20:00 without asking the model again
    And once a day, or a daily quota without a count, beside weekdays reads as those weekdays, and beside a clock alone as every day
    And a weekly count beside as many weekdays, two or more, reads as those weekdays: "twice a week on Mon and Thu"
    And a monthly count beside as many days of the month, two or more, reads as those days: "twice a month on the 1st and 15th"
    And all seven weekdays beside any other quota are the quota alone, as every day beside an interval is the interval alone by RM-SCHEDULE-003
    And any other quota beside weekdays or days of the month goes back to the model, even once a week beside
      one weekday or once a month beside one day of the month
    And a count without a period that equals the total is the total alone, and any other count
      without a period goes back to the model, told that a total is limit
    And that repair names the forms: a quota without days or time, after completion, or timing without a quota

  Scenario: SCH-END-025 — A repeating Schedule can end on a last day
    Given the Schedule "every day at 21:00 for 30 days", "every Monday until 30 November" or "twice a day for a week" on an Action or a Check
    Then the Scheduler reads the last day as a date, a weekday or how long, worked out in code by RM-END-036
    And the screen shows it, "Every day at 21:00, until 15.11.2026."
    And no instance opens for a slot after the last day, while an open instance stays open until it is answered
    And planning counts no work after the last day
    And a last day on a one-time Schedule, or one before its first time, goes back to the Scheduler's model to repair, and a Deadline has none

  Scenario: SCH-START-026 — A quota can start on a later day
    Given the Schedule "3 times a week from Monday for a month", read on Saturday 17.10.2026
    Then the Scheduler reads it as 3 a week from Monday 19.10.2026 through 18.11.2026, and the screen shows "3 per week from 19.10.2026, until 18.11.2026."
    And its first instance belongs to the week of 19.10.2026, and an answer given before that day counts in that week
    And planning counts no work before that day
    And a start without a year is the one nearest today by RM-END-036: "3 times a week from 12.10" read on 17.10.2026 has begun in the week of 12.10.2026
    And a date beside repetition after completion goes back to the Scheduler's model to repair

  Scenario: SCH-MONTH-027 — A Schedule can repeat on days of the month or by a monthly quota
    Given the Schedule "on the 1st and 15th of each month", "on the last day of each month at 18:00" or "three times a month" on an Action or a Check
    Then the Scheduler reads days of the month as numbers, worked out in code by RM-MONTH-037
    And days of the month without a clock are an appointment for that whole day by SCH-DAY-018, and the screen shows "On the 1st and 15th of each month, any time that day."
    And a monthly quota's period is the local calendar month, from its 1st to the 1st of the next, and the screen shows "0/3 answered for the month from 01.11.2026."
    And a month left short opens the next one with its whole quota, and nothing carries over
    And Remind on days of the month with a clock makes a Reminder on those days by SCH-REMIND-019

  Scenario: SCH-REMIND-028 — Remind fires its lead before each moment
    Given Remind 30 minutes before on an Action every day at 00:15
    Then it fires at 23:45 the day before, and /reminders says "every day at 00:15, 30 minutes before"
    And 1 hour before the last day of each month at 18:00 fires at 17:00 on that day, in a short month too
    And across a change of daylight saving time it fires that many minutes before the moment as the clock runs
    And on an interval it fires that long before each of its moments
    And a lead whose time has passed while the moment is still ahead fires at once
    And a repeat whose lead passed more than 120 minutes ago (REMINDER_CATCHUP_GRACE_MINUTES) waits for its next moment
    And what Safwa is handed when it fires says no lateness while the moment is still ahead,
      and how late it is once the moment has passed

  Scenario: SCH-REMIND-029 — Safwa can turn Remind on for an Action, a Goal or a Check
    Given the owner asks to be reminded of an Action, a Goal or a Check "an hour before"
    When the reminder part proposes Remind of 60 minutes on that item, never a new Reminder, by
      RM-AGENT-041
    Then the review screen shows Remind as "1 hour before"
    And Save turns Remind on 1 hour before each of its moments, by SCH-REMIND-028
    And Remind of 0 is at the time, and null turns Remind off
    And a new item asked for with Remind is proposed with it by the item's own tool, which takes
      remind only when it creates the item
    And Remind on an item whose Schedule has no clock still ahead goes back to the model,
      "Remind needs a Schedule with a time still ahead.", and nothing is proposed

  Scenario: SCH-PERIOD-031 — An open instance is on the period it is in, and a closing counts for it
    Given an Action every day, and a Check every day at 20:00
    Then a moment's period runs from the start of its day to the start of the next moment's day
    And an interval moment's period runs to the next moment, and a quota's is its day, week or month
    When a new period starts while an instance is open
    Then the same instance moves on to it, and the period it leaves gets no answer and no Missed
    When the Action is not done on Monday and is Done on Tuesday
    Then it counts for Tuesday, and the next instance is Wednesday's
    When the Check starts on Monday at 21:00 and is answered at 21:30
    Then it counts for Monday, and the next instance is Tuesday's at 20:00
    When the next is answered on Tuesday at 15:00, and the one after it at 18:00
    Then both count for Tuesday, and the next instance is Wednesday's at 20:00
    When a Check every 10 minutes from 12:00 is left unanswered until 12:25
    Then it is on 12:20 by then, its answer counts for 12:20, and the next instance is at 12:30
    And a closing before the Schedule's first day counts for its first period
    When a Check every day at 7:00 is Passed from Monday to Wednesday, left on Thursday, and Passed
      on Friday at 9:00 and on Saturday
    Then Friday's answer counts for Friday, and the five Passed are in a row: Thursday has no answer
      and no Missed
