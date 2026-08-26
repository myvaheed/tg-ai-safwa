Feature: Planning — the Sprint, and the mode without one
  Planning is what the workspace is in while no Sprint runs; there is no Today then. A Sprint is a
  fixed stretch of days with Success criteria that say what it must achieve, and it keeps one row
  per Action it ever had in scope. Everything counted about a Sprint is effort points, never a
  count of Cards.

  The Sprint is run by hand. Safwa reads it, and says where the owner does the rest themselves.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace the owner plans a Sprint in, and runs one Sprint at a time

  Scenario: PL-MODE-001 — The workspace is either planning a Sprint or running one
    Given no Sprint is running
    Then the workspace is in Planning
    And Today has no screen, no menu button and no command
    When a Sprint starts
    Then the workspace is in Sprint and Today is available again

  Scenario: PL-MODE-002 — The Sprint is the owner's to run, and Safwa only reads it
    Given the owner is talking to Safwa
    When they ask it to start the Sprint, finish it, or change its dates, its length or its Success criteria
    Then Safwa has no way to do any of it and says where the owner does it themselves
    And no proposal is ever written about a Sprint
    And starting and finishing happen on the Sprint screen, and the length and the capacity in Settings

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
    Given the Sprint length in Settings is 14 days (SPRINT_LENGTH_DAYS = 14)
    When the owner starts a Sprint
    Then it runs from the owner's today through the 14th day, that day included
    And its number is one higher than the highest number any Sprint has ever had

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
    And the next copy of a repeating Action is counted the same way

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
    Then the Sprint counts them as neither completed nor cancelled
    And finishing it as Cancelled instead is counted apart from Done

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
    Then Safwa closes the Sprint
    And everything still open keeps its stage
    When it is one minute before that midnight
    Then the Sprint is still running

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
    Then the planned one is a row with its effort, and the plan says what the whole plan costs
    And the Backlog is buttons, ten to a page (SPRINT_PLAN_PAGE_SIZE = 10), each with a way into the Sprint
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
    Given the capacity in Settings is 10 points and 13 points are planned
    Then wherever the plan's total effort is shown, the capacity is shown beside it, and the owner is told the plan is above it
    And the Sprint still starts
    When no capacity is set
    Then the total is shown and nothing is warned about

  Scenario: PL-PLAN-019 — The plan says when the owner is tapping too fast
    Given every row in the plan is a Telegram link, which Telegram counts against the owner's account rather than against Safwa
    When the owner taps 8 of them inside 10 seconds (PLAN_LINK_BURST_TAPS = 8, PLAN_LINK_BURST_SECONDS = 10)
    Then they are told what is happening and that Telegram can stop opening bots for hours
    And the tap they just made still opens what it points at
