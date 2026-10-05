Feature: One plain-language Schedule for Actions and independent Checks

  Scenario: SCH-QUOTA-001 — A calendar quota opens one current instance
    Given an Action scheduled five times a day
    When the fifth instance is completed
    Then one next instance belongs to the following local day
    And the plan reports five completed instances in the original day

  Scenario: SCH-WINDOW-002 — A week covered in part plans its share of the quota
    Given a Check scheduled three times a week without a clock
    When the Advisor queries Wednesday through Sunday of one week, and then Monday and Tuesday of it
    Then 2 answers are planned for the five days and 1 for the two: the quota times the days covered, divided by 7, rounded
    And no appointment time is invented

  Scenario: SCH-CHECK-003 — A scheduled Check is an independent observation series
    Given a Check with its own Schedule
    When it is answered Missed
    Then the answer counts as an observation and the next instance is opened
    And the Check cannot be attached to a Card

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

  Scenario: SCH-INTERVAL-009 — Early completion consumes its appointment
    Given an Action or Check repeating at a fixed interval
    When its future appointment is completed early
    Then the next appointment is later than the one just completed

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
    And a weekly quota's copy opens in Sprint while its week starts by the Sprint's last day
    And a copy due after the Sprint's last day opens in Backlog
    And an after-completion copy keeps Today or Sprint, and one from Backlog opens in Sprint
    When no Sprint is running
    Then a copy from Backlog stays in Backlog
    And a copy from Sprint or Today opens in Today when due today, keeps its stage when it repeats after completion, and otherwise opens in Sprint

  Scenario: SCH-RETRY-014 — A Schedule the model cannot read asks for other words
    Given the Scheduler's model gives no valid reading within its tool calls
    Then the owner is asked to say the Schedule in other words
    And nothing is saved

  Scenario: SCH-LIMIT-015 — An Action repeats at most five times a day
    Given a Schedule on an Action that plans more than 5 executions a day (ACTION_DAILY_EXECUTIONS_MAX = 5)
    Then the Scheduler asks whether to make it a Check instead or choose fewer, rather than saving it
    And a daily quota of 6, a weekly quota of 36 and an interval under 288 minutes are each asked about
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
    And Remind On makes a Reminder at the Schedule's moments, with the words "Remind is on for Action #12 «Stretch». Remind the owner about it." (REMIND_TEXT)
    And Remind Off deletes that Reminder
    And a quota, a whole-day appointment, repetition after completion, a Deadline without a time, or a one-time moment already past opens the editor at once

  Scenario: SCH-REMIND-020 — Remind's Reminder follows its item
    Given Remind is on for a repeating Action or Check
    When it is finished before its appointment
    Then the Reminder names the next instance and fires first at that instance's appointment
    And an instance left overdue is reminded at the Schedule's next moment, and at each one after it
    And a changed Schedule moves the Reminder to it, and a Schedule without a clock deletes it
    And a renamed item rewrites the Reminder's words
    And finishing a one-time Action, closing a Goal, or deleting the item deletes the Reminder
    And deleting the Reminder in /reminders turns Remind off
