Feature: Reminders
  A Reminder is instruction text plus a schedule, and nothing else. The owner never structures a
  schedule and neither does the model: timing arrives as plain words, one session resolves those
  words into parameters, and the schedule is computed from them in code. When one comes due the
  system does exactly one thing — it hands the text to the Advisor as a request.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace with a local timezone and an Advisor that proposes rather than writes

  Scenario: RM-SCHEDULE-001 — Timing reaches the system as free text and is computed in code
    Given the board subagent proposes a Reminder with when in plain words
    When the proposal is prepared
    Then a setup session resolves those words into schedule parameters
    And the schedule itself is computed from those parameters in code
    And the mutation tool accepts no schedule field of any kind

  Scenario: RM-SCHEDULE-002 — A phrase that does not determine a schedule asks the owner
    Given the owner says "remind me every morning"
    When the setup session runs
    Then it answers that the phrase does not determine a schedule, with one question to ask
    And that question comes back to the model as a retryable tool error
    And no proposal row and no Reminder exist

  Scenario: RM-SCHEDULE-003 — A date is always a start date; a time is a fire clock only next to weekdays
    Given resolved parameters
    When the schedule is built
    Then an interval alone starts now and repeats
    And an interval with a time starts at that clock's next occurrence
    And an interval with a date and a time starts at that exact moment
    And weekdays with a time fire weekly at that clock, or daily when all seven are given
    And weekdays with a date fire on the first matching day on or after it
    And a date with a time is a single occurrence
    And a time alone is its next occurrence

  Scenario: RM-SCHEDULE-004 — A recurrence that started in the past is already running
    Given a repeating schedule whose start is in the past
    When its first fire is computed
    Then it is the next occurrence from now
    And no missed occurrence is created

  Scenario: RM-SCHEDULE-004 — A single occurrence in the past cannot be satisfied
    Given a single occurrence whose moment has already passed
    When the schedule is built
    Then it is refused, because that moment cannot be satisfied

  Scenario: RM-SCHEDULE-005 — Parameters that cannot become a schedule are named, never corrected
    Given parameters that contradict each other or fall outside the allowed range
    When the schedule is built
    Then the reason is stated and no schedule is produced
    And an interval below 5 minutes (REMINDER_MIN_INTERVAL_MINUTES), weekdays given together with
      an interval, quiet windows on a schedule that is not an interval, quiet windows leaving no
      time of day, a date without an hour, and nothing at all are each such a reason

  Scenario: RM-CLOCK-006 — A stored fire time is a local wall clock, not a UTC offset
    Given a daily Reminder at 08:30 local
    When the local zone crosses a daylight-saving shift
    Then the next fire is still 08:30 local
    And a quiet window edge lands at the same local clock on both sides of the shift

  Scenario: RM-QUIET-007 — A quiet window suppresses hours of the day, and its end is exclusive
    Given an interval Reminder with a quiet window
    When a computed fire lands inside that window
    Then it moves to the window's end
    And a window written as one range wrapping midnight and the same window written as two ranges
      meeting at 00:00 behave identically
    And chained windows push a candidate through all of them

  Scenario: RM-WRITE-008 — No Reminder is written without a proposal
    Given the model proposes a Reminder
    When the owner sees the review screen
    Then it names the resolved schedule rather than the words the owner used
    And Save creates the Reminder with exactly that schedule and its first fire
    And Discard leaves no Reminder and no trace of one

  Scenario: RM-WRITE-009 — An edit that carries no timing leaves the schedule alone
    Given a Reminder the model is editing
    When it sends new instruction text and no timing
    Then no setup session runs and no schedule column changes

  Scenario: RM-WRITE-009 — Editing the text by hand leaves the schedule alone
    Given the owner opens a Reminder and edits its text
    When they send the new text
    Then the Reminder keeps its next fire
    And empty text is refused and the Reminder view comes back unchanged

  Scenario: RM-WRITE-010 — Deletion is the only off switch
    Given a Reminder
    When it is removed, by the owner from its screen or by an approved model proposal
    Then the row is gone and it stops firing immediately
    And there is no archive and no disabled state to return from
    And removal is one confirmation, not the permanent-deletion screen a Card tree gets

  Scenario: RM-FIRE-011 — A fired Reminder hands its text to the Advisor as a request
    Given a Reminder comes due
    When it is escalated
    Then the Advisor receives the instruction text, its schedule and its firing history as one
      request
    And it reads the same canonical dialogue an owner message would
    And its answer is registered as a proactive bot message, not as a reply
    And nothing between the poll and the Advisor decides what the Reminder means

  Scenario: RM-FIRE-012 — One poll is one turn
    Given 5 Reminders are due at the same poll
    When the poll runs
    Then the 3 oldest go to the Advisor as one request (REMINDER_FIRE_BATCH = 3)
    And the other 2 stay due, for the next poll 30 seconds later (SCHEDULER_POLL_SECONDS = 30)

  Scenario: RM-FIRE-013 — A schedule advances only after the answer was delivered
    Given a due Reminder
    When the gate is closed, or the turn fails, or the owner takes the lease mid-turn
    Then no schedule is advanced and the Reminder is still due at the next poll, 30 seconds later
      (SCHEDULER_POLL_SECONDS = 30)
    And only a delivered answer advances it, records the firing and counts it

  Scenario: RM-FIRE-014 — Advancing a schedule is bookkeeping, not an owner-visible change
    Given an answer was delivered and the schedules advance
    When the workspace revision is read
    Then it is unchanged, so no pending proposal and no in-flight answer is invalidated by a fire

  Scenario: RM-FIRE-015 — A repeat advances from its scheduled moment
    Given a repeating Reminder due at 08:30 and a turn that takes four minutes
    When the answer is delivered
    Then the next fire is computed from 08:30
    And a slow turn does not push every later fire out

  Scenario: RM-FIRE-016 — A single occurrence fires exactly once, however late
    Given a single-occurrence Reminder that is overdue
    When the poll finds it
    Then it fires, and the request says how late it is
    And it deletes itself only after the answer was delivered
    And it never produces a second escalation

  Scenario: RM-GATE-017 — Nothing escalates on top of an unanswered question
    Given a Reminder is due
    When the Advisor is generating, or a proposal is pending, or an approval batch or a claimed
      session is still unresolved
    Then nothing is escalated and nothing is queued
    And the Reminder stays due for the next poll

  Scenario: RM-GATE-018 — The owner always wins
    Given a background escalation holds the generation lease
    When the owner sends a message
    Then the escalation is cancelled and the owner's message is answered
    And its half-finished answer is discarded and nothing is published
    And the Reminder was never advanced, so it is still due

  Scenario: RM-CATCHUP-019 — A repeat missed inside the grace still fires, once
    Given a repeating Reminder whose stored fire time is 90 minutes overdue
    When the poll finds it
    Then it fires once, and that one fire is the whole catch-up
      (90 is under REMINDER_CATCHUP_GRACE_MINUTES = 120)

  Scenario: RM-CATCHUP-019 — A repeat missed past the grace rolls forward in silence
    Given a repeating Reminder whose stored fire time is 3 hours overdue
    When the poll finds it
    Then its schedule rolls forward silently and the owner is told nothing
    And however often it repeats: one every 5 minutes, offline for 3 hours, escalates nothing at
      all rather than 36 times

  Scenario: RM-START-020 — Startup fixes what downtime made wrong, and only that
    Given the bot starts
    When Reminders are reconciled
    Then a wall-clock schedule whose stored fire no longer matches its local clock is rebuilt
    And a repeat overdue past the grace rolls forward
    And a repeat overdue inside the grace is left for the first poll to fire
    And a single occurrence is never moved

  Scenario: RM-POLL-021 — The poll outlives its own failures
    Given one poll raises
    When the next poll comes round 30 seconds later (SCHEDULER_POLL_SECONDS = 30)
    Then it runs, and the failure was logged rather than lost
    And Reminders do not silently stop firing for the life of the process

  Scenario: RM-SYSTEM-022 — A Reminder no owner set belongs to Safwa
    Given a Reminder Safwa derived rather than the owner set — the Diary trigger, or a Sprint's
      own end warning
    When the owner opens /reminders, or the model reads Reminders
    Then it is not there
    And every edit, reschedule and delete path refuses it and says where to change it
    And it fires exactly like any other Reminder
    And the feature that created it is what removes it: Settings for the Diary trigger, finishing
      the Sprint for its end warnings

  Scenario: RM-UI-023 — /reminders is a list, a detail and two actions
    Given the owner opens /reminders
    Then each Reminder reads as its schedule and the start of its text, next fire first
    And opening one shows its schedule, its next fire, its firing history and its full text
    And the only actions are editing the text and deleting it
    And there is no way to create a Reminder here and no way to edit a schedule here
    And an empty list says the advisor is who creates them

  Scenario: RM-READ-024 — The Advisor reads Reminders through the view, never the prompt prefix
    Given Reminders exist
    When the model needs to know about them
    Then it queries the read-only view, which gives it the schedule columns as they are stored
    And the next fire reads as the owner's local wall clock, not as the stored UTC instant
    And no Reminder appears in the assembled prompt prefix, whose next fire moves on every fire
