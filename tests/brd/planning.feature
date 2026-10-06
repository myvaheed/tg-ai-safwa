Feature: Planning — the Sprint, and the mode without one
  Planning is what the workspace is in while no Sprint runs; Today keeps its Actions then. A Sprint is a
  fixed stretch of days with Success criteria that say what it must achieve, and it keeps one row
  per Action it ever had in scope. Sprint totals count Actions, or effort points while Effort Points are on; the screen's list
  selectors count Actions in each list.

  The Sprint is run on its screen or in words, and both go through the same operations.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace the owner plans a Sprint in, and runs one Sprint at a time

  Scenario: PL-MODE-001 — The workspace is either planning a Sprint or running one
    Given no Sprint is running
    Then the workspace is in Planning
    And Today keeps its menu button, its command and its screen, and Actions move into and out of it as in a Sprint
    And the Planning screen shows no Today list
    When a Sprint starts
    Then the workspace is in Sprint

  Scenario: PL-MODE-002 — The Sprint is run on its screen or in words, through the same operations
    Given the owner is talking to Safwa
    When they ask it to start the Sprint, finish it, or set the next Sprint's Success criteria,
      length or capacity
    Then Safwa shows that change on a review screen with Save and Discard
    And a Sprint about to start is shown with its Success criteria, its first and last day and
      its length in days, which is the next Sprint's length (PL-START-005)
    And a change to the next Sprint shows each value it changes, what it is and what it becomes
    And Save does what the Sprint screen's button does, with the same refusals
    When a Sprint is running and they ask to change its Success criteria
    Then it is refused with that reason, and nothing is proposed
    When they ask it to change a Sprint's dates, pause it, extend it or bring a finished one back
    Then Safwa says there is no way to, in words or on a screen (PL-END-012)

  Scenario: PL-CRITERIA-003 — A Sprint starts with words and with work
    Given the next Sprint has no Success criteria and nothing planned
    Then Planning does not offer to start it
    When the owner sends Success criteria that are only spaces
    Then it is refused and nothing is saved
    When the owner has written Success criteria but no Action is in Sprint or Today
    Then starting is refused for that reason
    When at least one Action is in Sprint or Today as well
    Then Planning offers to start the Sprint

  Scenario: PL-CRITERIA-004 — Success criteria outlive the Sprint they were written for
    Given a Sprint started with Success criteria
    When it ends
    Then those words are still there as the draft for the next Sprint, to edit or reuse
    And Safwa reads them as a draft and says no Sprint is running

  Scenario: PL-START-005 — Starting a Sprint fixes its days and its number
    Given the next Sprint's length is 14 days, as in a new workspace (SPRINT_LENGTH_DAYS = 14)
    When the owner starts a Sprint
    Then it runs from the owner's today through the 14th day, that day included
    And its number is yy.MM-xx: the month it started in, then its place in that month
    And that place is one past the highest ever used in that month, so a deleted Sprint
      never hands its number to another
    And a Sprint that runs into the next month keeps the month it began in

  Scenario: PL-SCOPE-006 — Starting a Sprint takes what is already planned, at the effort it has then
    Given an Action of 3 points in Sprint, an Action of 5 points in Today, and a Goal above them
    When the Sprint starts
    Then it committed to 8 points, and the Goal is not one of them
    When the owner later edits the 3-point Action to 8 points
    Then the Sprint still says it committed to 8 points

  Scenario: PL-SCOPE-007 — Work that joins a running Sprint is counted apart
    Given a running Sprint
    When the owner moves a 2-point Backlog Action into Sprint or Today
    Then those 2 points are counted as added, and what the Sprint committed to does not change
    And an Action created straight into Sprint or Today is counted the same way
    And a generated copy carries its series' remaining reserved executions instead of adding them twice (PL-REPEAT-032)

  Scenario: PL-SCOPE-008 — Work sent back to the Backlog is counted as removed, and bringing it back undoes that
    Given a running Sprint that committed to a 5-point Action
    When the owner moves it to the Backlog
    Then those 5 points are counted as removed
    When the owner moves it back into Sprint or Today
    Then they are not counted as removed any more

  Scenario: PL-SCOPE-009 — A Sprint records what each Action came to
    Given a running Sprint that committed to a 5-point Action
    When the owner finishes it as Done
    Then the Sprint counts those 5 points as completed
    When the owner reopens it
    Then the Sprint counts them as not completed

  Scenario: PL-CONTEXT-010 — Safwa is handed the Sprint's number, its dates and its Success criteria
    Given a running Sprint
    Then every turn hands Safwa its number, its planned dates and its Success criteria
    And none of the Sprint's counted effort is in what it is handed
    When no Sprint is running
    Then Safwa is told so instead, with the draft Success criteria for the next one

  Scenario: PL-WARN-011 — A Sprint warns the owner before it ends
    Given the owner starts a 14-day Sprint at 18:32
    Then Safwa warns them the day before the end date at 18:32, and again on the end date at 18:32
    And a 2-day Sprint is warned only on its end date (SPRINT_LENGTH_MIN_DAYS = 2)
    And the warnings are a daily check at the time the Sprint started, by AG-HOOK-039: a
      warning time that passed while Safwa was not running is not made up
    And they are an automatic reaction with a switch of its own in the Profile (PS-HOOKS-015)
    When the Sprint ends
    Then both warnings are gone

  Scenario: PL-END-012 — The owner ends the Sprint, and on its last day the button says so
    Given a running Sprint with an unfinished Action in Today
    Then the Sprint screen offers to finish it early
    When the owner finishes the Sprint
    Then the workspace is in Planning
    And that Action is still in Today, already planned for the next Sprint
    And there is no way to pause a Sprint, to extend it, or to bring a finished one back
    When it is the Sprint's last day
    Then the same button reads "Finish Sprint"

  Scenario: PL-END-013 — A Sprint nobody closed closes itself
    Given a running Sprint whose end date has passed
    When the owner's own midnight passes, whatever the clock says elsewhere
    Then Safwa closes the Sprint, by a daily check at midnight (AG-HOOK-039)
    And everything still open keeps its stage
    When it is one minute before that midnight
    Then the Sprint is still running
    And a midnight Safwa was not running for is made up on its next start, so the Sprint is closed then

  Scenario: PL-END-014 — A Sprint ending is when the workspace is tidied
    Given Cards and Checks that closed two Sprints ago (ARCHIVE_AFTER_SPRINTS = 2)
    When a Sprint ends, whether the owner closed it or it closed itself
    Then those Cards and Checks are archived
    And while the workspace is in Planning nothing is archived, however long it stays there

  Scenario: PL-END-015 — A Sprint that ended is handed to Safwa, and Safwa is the one who says so
    Given a Sprint that has just ended, by the owner's own hand or at midnight
    Then a short summary of it is written from the Sprint's own record, without Safwa reading anything
    And that summary is handed to Safwa the way a Reminder that comes due hands over its words
    And Safwa writes one message to the owner about how the Sprint went, ending with a link named "Sprint retro"
    And the owner's next question about the Sprint is answered from that message, not from a fresh trip to the tables
    When the owner taps that link
    Then a Sprint retro screen for that Sprint opens, empty until the retrospective feature fills it

  Scenario: PL-PLAN-016 — The plan is the Sprint as a table and the Backlog as the keyboard
    Given one Action is planned and twelve are in the Backlog
    Then the planned one is a row, and the plan says how many Actions are planned
    And with Effort Points on, the row and plan also show their effort
    And the Backlog is full-width buttons, ten to a page (SPRINT_PLAN_PAGE_SIZE = 10)
    When the owner taps a Backlog Action
    Then it goes into the Sprint and the plan is redrawn with the same page and filters,
      without opening the Card
    When the owner taps a planned Action's Return
    Then it goes back to the Backlog and the plan is redrawn in place, without a second screen

  Scenario: PL-PLAN-017 — Filters narrow the Backlog, and every picked Request has to match
    Given two saved Requests, and a Backlog Action only one of them returns
    When the owner picks both
    Then that Action is not offered, and only Actions both Requests return are
    When nothing matches
    Then the keyboard says how many of the Backlog matched instead of going blank
    And a Request that was deleted since it was picked is dropped, and the rest still filter

  Scenario: PL-PLAN-018 — The plan's cost is shown against the capacity
    Given Effort Points are on in the Profile
    Given the next Sprint's capacity is 10 points and 13 points are planned
    Then wherever the plan's total effort is shown, the capacity is shown beside it, and the owner is told the plan is above it
    And the Sprint still starts
    When no capacity is set
    Then the total is shown and nothing is warned about

  Scenario: PL-PLAN-019 — The plan says when the owner is tapping too fast
    Given every row in the plan is a Telegram link, which Telegram counts against the owner's account rather than against Safwa
    When the owner taps 8 of them inside 10 seconds (PLAN_LINK_BURST_TAPS = 8, PLAN_LINK_BURST_SECONDS = 10)
    Then they are told what is happening and that Telegram can stop opening bots for hours
    And the tap they just made still opens what it points at

  Scenario: PL-CONTEXT-020 — Today's Actions are handed over, in Planning as in a Sprint
    Given Actions in Today
    Then Safwa is handed every Action in Today, with its effort only while Effort Points are on
    And a Card that is not an Action, or is not in Today, is not among them
    And they come in the order Today has (PL-KEY-025)
    And they are handed over the same way whether a Sprint is running or not

  Scenario: PL-HARDTIME-021 — An Action whose Schedule the plan does not hold is brought up
    Given a Sprint runs, and open Actions carry a compiled Schedule
    When the Sprint starts, and each day when the Profile's Morning time passes (MORNING_TIME_DEFAULT = "09:00"), and the chat is free
    Then the Advisor is asked once, in one message about all of them, naming each with its next appointment or its daily quota and the stage it is in: the ones whose appointment falls by the Sprint's planned last day and that are in Backlog, the ones whose appointment is today or tomorrow (SCHEDULE_NOTICE_DAYS = 1) and that are not in Today, and the ones with a daily quota left for today that are not in Today
    And it is asked whether to take the ones due today or tomorrow into Today and the others into the Sprint; nothing is moved before the owner's answer
    And the list is read when the question is about to be said: one already in Today, one in the Sprint whose appointment is later than tomorrow, one finished or archived, one whose appointment has passed — earlier today as much as yesterday — and one whose daily quota is met for today are left out; with none left, nothing is asked
    And in Planning, with no Sprint running, the morning asks nothing
    And a day is the workspace's local day

  Scenario: PL-ENERGY-022 — A Sprint that leaves out a kind of energy the Backlog has is brought up
    Given a Sprint starts, and the chat is free
    When one of the four energy types (Physical, Cognitive, Emotional, Spiritual) is on no open Action in the Sprint and is on an open Action in Backlog
    Then the Advisor is asked once, in one message about every such type, naming for each up to three Backlog Actions that carry it (ENERGY_CANDIDATES = 3), Critical first
    And the same is asked for the Rest category: no open Action of it in the Sprint while Backlog holds one
    And it is asked to suggest one of each into the Sprint, for a spread of energy over the Sprint; nothing is moved before the owner's answer
    And a type on no Backlog Action either is not mentioned; with nothing missing, nothing is asked
    And the list is read when the question is about to be said: a type the Sprint has gained by then is left out, and with the Sprint ended by then nothing is asked

  Scenario: PL-KEY-023 — The Actions a Sprint's Success criterion rests on are marked without asking
    Given a Sprint starts with a Success criterion, or an Action joins the running Sprint — moved or created into it, or brought back from Backlog
    When that is saved
    Then in the background the Sprint's open Actions are read to the model in batches of ten (KEY_BATCH = 10), each as its number in the batch and its title, with the Success criterion, and the batches are asked at once
    And the model answers each batch with one call, mark_key_actions, naming the numbers the criterion rests on; each named Action is marked key to this Sprint, the others in that batch marked not key
    And an Action not yet answered about carries no mark: not marked is not the same as marked not key
    And a batch whose answer is not that call, or names a number not in its list, is logged and leaves the marks of its Actions as they were; the other batches' answers are written
    And an answer is written only to an Action still open in the running Sprint under the title the model read; one renamed meanwhile keeps the mark a later answer gave it
    And an Action joining the running Sprint is asked about alone, the same way, and the marks of the others stand
    And the owner is told nothing and nothing is proposed; the marks are read by the Today order (PL-KEY-025) and the warning (PL-KEY-024)
    And the marks are handed on as one saved change once any is written; a marking that wrote none hands nothing on

  Scenario: PL-KEY-024 — A Sprint left with no key Action to reach its Success criterion is warned about
    Given a Sprint runs, and its Actions have been marked
    When the marking finds no key Action, or an Action leaves the Sprint unfinished — moved to Backlog or deleted — and the chat is free
    Then, with no key Action open in the Sprint and none finished in it, the Advisor is asked once to say in one message that the Success criterion does not look reachable with what is planned, and to propose nothing
    And the Sprint is read when the word is about to be said: with a key Action finished by then, or one open in the Sprint again, or an open Action not marked yet, or no Sprint running, nothing is said

  Scenario: PL-KEY-025 — Today is ordered by what the day cannot move
    Given Actions in Today
    Then the Today screen and the Today list the Advisor reads put first the ones whose Schedule appointment is today or tomorrow (SCHEDULE_NOTICE_DAYS = 1), then Critical ones, then key ones, then the rest
    And within each group a Card with an appointment comes first, then the more important one, then the one written earlier
    And a day is the workspace's local day

  Scenario: PL-ASK-026 — A question about the Sprint is answered from the Sprint as it stands
    Given a Sprint is running on its 5th day of 14
    When the owner asks how long the Sprint is or how many days are left
    Then Safwa answers from the workspace state it is handed that turn: the Sprint's own dates,
      its length, today's day of it and the days left after today
    And while Effort Points are on, that state holds the capacity the Sprint started with
      (PL-CAPACITY-027)
    And in Planning it answers with the length a Sprint started today would have, its dates,
      and its capacity while Effort Points are on

  Scenario: PL-CAPACITY-027 — A Sprint keeps the capacity it started with
    Given Effort Points are on in the Profile
    Given the next Sprint's capacity is 20 points
    When a Sprint starts, by the button or by Save
    Then the Sprint keeps 20 points as its capacity
    When it ends and the owner sets the next Sprint's capacity to 30
    Then that Sprint still says 20
    And a Sprint started with capacity off keeps none, and an average over capacity leaves it out

  Scenario: PL-SCREEN-028 — The running Sprint shows one chosen list without duplicate Action buttons
    Given a running Sprint with Actions in Sprint and Today, completed Actions, and blocked Actions
    When the owner opens Sprint
    Then it shows its number, dates, local day of its length, Success criteria, and taken and done Actions, or EP while Effort Points are on
    And Remaining lists only Actions still in Sprint
    And Today lists today's Actions in Today's order
    And Done lists only Actions completed in this Sprint
    And Blocked lists open blocked Actions in Sprint and Today with their reasons
    And the selectors show each list's Action count and mark the selected list
    When the owner selects a list
    Then the same message shows that list, with no separate button for each Action
    And each Action's title is a link that opens its Card in place of the Sprint screen
    And the Card's Back draws the Sprint screen again, on the same list and page
    And an empty list explains that it is empty without saying the Sprint has ended

  Scenario: PL-SCREEN-029 — Turning a Sprint list's page keeps the selected list
    Given a running Sprint whose selected list has more than 5 Actions (PAGE_SIZE = 5)
    When the owner turns its page
    Then the same message shows the next page of that list and its page number
    When the owner selects another list
    Then it opens at its first page

  Scenario: PL-EP-030 — Planning and Sprint work without effort estimates
    Given Effort Points are off, with estimated and unestimated Actions in the workspace
    Then the plan and Sprint show Action counts, with no EP or capacity controls or warnings
    And the plan has no EP column, and its Action buttons show titles alone
    And a Sprint can start, Actions can join and finish, and each scheduled execution is counted once
    When Effort Points are switched on
    Then missing estimates are named beside partial totals and are never presented as zero load

  Scenario: PL-REPEAT-031 — Planned Actions and EP include calendar executions
    Given a 5 EP Action scheduled once a day, selected for a next Sprint of 3 days
    Then Planning, its proposal preview and AI context show 3 Actions and 15 EP
    And changing the next Sprint's length changes the plan's quantity, not a running Sprint's dates
    And the window starts today: an Action that joins a running Sprint counts only the days left, today among them
    And a week the window covers in part counts its share of the weekly quota by the days covered, rounded, and never more than the week has left of it
    And a current overdue appointment counts once, and an Action on the plan counts at least once, even when its appointment falls outside the window
    And the weighted load is compared with capacity and still counts executions with EP off

  Scenario: PL-REPEAT-032 — Generating a repeat consumes its reservation without adding scope twice
    Given a Sprint reserves 3 executions of a 5 EP Action
    When it finishes once and opens its next copy
    Then committed load stays 3 Actions and 15 EP, with 1 Done Action and 5 Done EP
    And its successor carries the remaining 2 executions, the same scope, key mark and frozen unit estimate
    And a series added during the Sprint carries its whole quantity as added only once
    And removing or returning the open copy removes or restores its remaining quantity
    And an actual execution beyond the reservation counts as added once
    And executions taken and not done stay taken and undone; only taking the open copy out of the Sprint counts as removed
    And planned Category, Energy, key, blocked and remaining quantities use the same execution count
    And actual Done, tracked time and EP of finished copies are not multiplied

  Scenario: PL-REPEAT-033 — Today and its morning plan account for repeated executions
    Given an Action of 5 EP scheduled five times a day in Today
    Then Today load is 25 EP and the overload check names five executions
    When one copy finishes
    Then the remaining four cost 20 EP and today's completed 5 EP keep the whole day at 25 EP
    And a second morning read does not add the successor to that day's plan again
    And Home and AI context show the remaining daily executions
    And retro keeps 5 planned executions that morning and 1 actual completion

  Scenario: PL-REPEAT-034 — Unknown timing is explicit and a Schedule change refreshes the forecast
    Given a selected Action repeats after each completion, with no date
    Then planned totals are marked as lower bounds, without completion percentages or planned shares
    When its Schedule is set or edited
    Then only the open copy's quantity is refreshed, with completed results and unit EP snapshots unchanged
    And a Schedule set on a Card in Today rechecks its load through the existing Today hook
    And clearing Schedule restores the ordinary one-execution quantity

  Scenario: PL-LENGTH-035 — The next Sprint's length is set in Planning, between 2 and 60 days
    Given the workspace is in Planning
    Then the Planning screen shows the next Sprint's length and the dates it would run if started today
    When the owner sets it, on that screen or in words, to a whole number of days from 2 through 60
      (SPRINT_LENGTH_MIN_DAYS = 2, SPRINT_LENGTH_MAX_DAYS = 60)
    Then every Sprint started after that runs that many days, until the length is changed
    But 1 day and 61 days are refused, and the length keeps what it had
    And while a Sprint runs, setting it is refused: a running Sprint keeps its dates (PL-MODE-002)

  Scenario: PL-CAPACITY-036 — The next Sprint's capacity is set in Planning, a number of points or off
    Given Effort Points are on and the workspace is in Planning
    Then the Planning screen shows the next Sprint's capacity
    When the owner sets it, on that screen or in words, to a positive number of effort points, a half included
    Then it is accepted, and off means no capacity at all
    But zero and a negative number are refused
    And while a Sprint runs, setting it is refused: the Sprint keeps the capacity it started with (PL-CAPACITY-027)
    And with Effort Points off the screen shows no capacity, and setting it in words is refused (PS-EP-021)
