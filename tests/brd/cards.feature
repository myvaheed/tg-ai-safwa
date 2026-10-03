Feature: Cards
  A Card is one thing the owner means to do. It is a Goal, a Subgoal or an Action, and those three
  form a strict tree: a Goal at the root, Subgoals under it, Actions at the bottom doing the work.
  What a Card may carry depends on which of the three it is.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where a Card can be written by the owner or proposed by Safwa

  Scenario: CD-KIND-001 — A Card is a Goal, a Subgoal or an Action, and it stays the one it was created as
    Given a Card exists as a Subgoal
    When the owner or Safwa tries to make it an Action
    Then the change is refused and the Card is still a Subgoal
    And the only way to have an Action instead is to create one
    And the kinds that change without being asked to are a Subgoal whose Goal was deleted on
      its own, by CD-DELETE-025, and a Goal placed under a Goal, by CD-TREE-002

  Scenario: CD-TREE-002 — A Goal is created root-level, and one placed under a Goal becomes a Subgoal
    Given the owner has Goals "Health" and "Life"
    When a Goal is proposed with a parent
    Then it is refused, and the refusal says a Goal is created root-level
    When "Health" is placed under "Life"
    Then "Health" is a Subgoal under "Life", and its history records the change of kind
    And the review screen showed the kind changing before Save
    When a Goal with a Subgoal under it is placed under a Goal
    Then it is refused, and the refusal says a Goal with Subgoals under it cannot become a Subgoal
    And a Goal placed under a Subgoal or an Action is refused as a Subgoal would be

  Scenario: CD-TREE-003 — A Subgoal belongs to a Goal
    Given a Goal "Health" and a Subgoal "Sleep better"
    When "Sleep better" is placed under "Health"
    Then it is placed there
    When another Subgoal is placed under "Sleep better"
    Then it is refused, and the refusal says a Subgoal may only be placed under a Goal
    And a Subgoal written with no parent is refused the same way, and so is taking its
      parent away
    And no screen offers Subgoal as a kind, because no screen sets a parent

  Scenario: CD-TREE-004 — An Action belongs to a Goal, a Subgoal or no one, and nothing belongs to an Action
    Given a Goal, a Subgoal under it, and an Action "Buy a pillow"
    When "Buy a pillow" is placed under the Goal, then under the Subgoal, then under nothing
    Then each of the three is accepted
    When any Card is placed under "Buy a pillow"
    Then it is refused, and the refusal says an Action cannot have children

  Scenario: CD-TREE-005 — A Card's place in the tree is only ever set by a proposal the owner saved
    Given the owner is looking at a Card
    When they look for a way to move it under another Card, or to give it a child
    Then there is none: the screens create, edit, link, and move a Card between stages
    And the only path that changes a parent is a Card proposal the owner saved, by PR-WRITE-002

  Scenario: CD-TREE-006 — A parent that is archived, or that is not there, is not a parent
    Given a Goal that the owner archived
    When a Card is proposed under it, or an existing Card is moved under it
    Then it is refused, and the Card keeps the parent it had
    And a parent that matches no Card is refused the same way

  Scenario: CD-FIELD-007 — Effort, categories, energy and Blocked belong to an Action alone
    Given a Goal is being written, by hand or as a proposal
    When effort, a category, an energy type, or Blocked is set on it
    Then the Goal is saved without them, rather than refused
    And the screens never offer those controls for a Goal or a Subgoal
    And the same holds for a Subgoal, and for a change to one that already exists
    And a proposed change that was nothing but those fields is refused instead of saved empty

  Scenario: CD-EFFORT-008 — An Action may say what it costs, on the one scale
    Given an Action is being written
    When it is saved with a number off the scale
    Then it is refused (EFFORT_POINTS = 0.5, 1, 2, 3, 5, 8, 13)
    And an Action saved without an estimate is accepted, by hand and in a proposal, while Effort Points are on or off
    And an Action without an estimate can be edited, finished and repeated
    And a valid draft with no estimate still offers Save
    And a rung says how the owner will be able to carry on afterwards, never how long the
      work takes (EFFORT_RUNGS)
    And the screens offer each rung with that wording, and the model's field description
      spells the same scale

  Scenario: CD-TITLE-009 — A Card has to be called something
    Given a Card is being written or renamed
    When the title is empty, or nothing but spaces
    Then it is refused and the Card keeps the title it had
    And a title that was saved with spaces around it is stored without them

  Scenario: CD-BLOCKED-010 — A blocked Action has to say why, and unblocking takes the reason with it
    Given an Action is marked blocked
    When it is saved with no reason written
    Then it is refused, and the refusal says a blocked Action needs a reason
    When the same Action is later unblocked
    Then the reason goes with it, and the Action no longer shows a warning

  Scenario: CD-STAGE-011 — A new Card starts in the Backlog and can never be born closed
    Given a Card is being written and no stage was chosen
    Then it starts in Backlog
    When a Card is written straight into Done
    Then it is refused: a new Card starts in Backlog, Sprint or Today

  Scenario: CD-LINK-012 — A Card is created with all of its Values, Tags and Checks, or the Card is not created at all
    Given a Card is written with the Value "Health", the Tag "home" and the Check "Slept 7 hours"
    When the Check has been archived, or any one of the three no longer exists
    Then no Card appears, and neither do the two links that were fine
    And the owner is told which one could not be linked
    And nothing is left half-written for them to clean up

  Scenario: CD-STAGE-013 — Only an Action has a stage
    Given a Goal, a Subgoal under it, and an Action under the Subgoal
    When the owner moves the Action to Today
    Then it is in Today
    When anything tries to move the Goal or the Subgoal
    Then it is refused before anything is written, in a proposal and on a screen alike
    And no screen offers a Goal or a Subgoal a stage control

  Scenario: CD-STAGE-014 — A Goal shows the stage of the Actions under it
    Given a Goal with one Action in Backlog and one in Sprint
    Then the Goal shows Sprint
    When one of them moves to Today
    Then the Goal shows Today, and so does every Card between them
    And Today wins over Sprint, and Sprint wins over Backlog
    And an Action anywhere in the branch counts, not the direct children only
    And a Goal with no Action anywhere under it shows Backlog

  Scenario: CD-STAGE-015 — Goals and Subgoals close only by explicit choice
    Given a Goal whose children have all been finished
    Then the Goal stays open in Backlog until the owner closes it with Done or saves a completion proposal
    And closing a Subgoal does not close its Goal
    And closing a Goal does not close its Subgoals
    And a Goal or Subgoal with open Actions cannot be closed
    And a Goal with one Done Action and one live Action shows the live one's stage
    And a Subgoal with nothing in it holds the Goal above it in Backlog
    And a Goal with nothing under it stays in Backlog until explicitly closed
    And a closed Goal or Subgoal may be reopened explicitly without reopening its Actions

  Scenario: CD-STAGE-016 — Done comes from finishing, not from moving
    Given an Action in Today
    When anything tries to move it straight to Done
    Then it is refused, in a proposal and on a screen alike
    And finishing it settles its Checks, its Sprint result and its repeat in the same act

  Scenario: CD-STAGE-017 — Reopening an Action undoes what closing it did
    Given an Action the owner finished as Done, so it has a completion time
    When the owner reopens it
    Then the completion time is cleared
    And the Checks it was closed on are given back to it, by CH-REOPEN-012
    And the Goal above it shows a live stage again
    And every closed parent is reopened, clearing its completion time and resetting its Checks
    And a new open Action under a closed branch does the same
    Given instead an Action that repeats and has been finished
    When anything tries to reopen it
    Then it is refused

  Scenario: CD-BLOCKED-018 — Only an Action can be marked blocked
    Given a Goal, a Subgoal and an Action
    When the Action is marked blocked, with a reason
    Then it is blocked, and the reason is the words that were given
    When anything tries to mark the Goal or the Subgoal blocked
    Then it is refused before anything is written, in a proposal and on a screen alike
    And no screen offers a Goal or a Subgoal a Blocked control

  Scenario: CD-BLOCKED-019 — A Goal shows the blocked Actions under it
    Given a Goal with a blocked Action somewhere underneath it
    Then the Goal reads as blocked, on its screen and to Safwa alike
    And its screen names each blocked Action and quotes the reason that Action gave
    And the Goal itself has no reason of its own
    When the Action is unblocked, finished, archived or deleted
    Then the Goal stops reading as blocked

  Scenario: CD-BLOCKED-020 — Being blocked does not stop anything
    Given a blocked Action in Today
    When the owner moves it, or finishes it
    Then it moves and it finishes
    And the reason is on the screen the owner lands on, not in a message the next screen overwrites

  Scenario: CD-EFFORT-021 — A Goal shows the effort of the Actions under it
    Given Effort Points are on in the Profile
    Given a Goal with three Actions of 5 points under it
    Then the Goal shows 15
    When one of them changes to 8
    Then the Goal shows 18
    And a Subgoal between them shows the total of its own branch
    And a Goal and a Subgoal still refuse an effort set by hand
    And a Goal with no Action under it shows no effort

  Scenario: CD-ARCHIVE-022 — A closed Card is archived two Sprints later
    Given an Action that was finished during the Sprint that has just ended
    When two Sprints have ended since the one it closed in
    Then it is archived without anyone asking
    And a Card still open is never archived, however old it is
    And a Goal whose branch is not finished is not archived either

  Scenario: CD-ARCHIVE-023 — An archived Card is marked, not left out
    Given a Goal with two finished Actions under it, one of them archived
    Then the archived one shows under its Goal marked "[📦]", and the lists by stage leave it out
    And the effort of both is still counted, and both still count as Cards that were completed
    And the Sprint it closed in still reports it

  Scenario: CD-ARCHIVE-024 — Only a closed Card can be archived by hand
    Given a live Action
    When the owner or Safwa asks for it to be archived
    Then it is refused: only a Card that is Done may be archived
    And a Goal a child keeps out of Done is refused the same way, and nothing under it is archived
    Given instead an Action that is Done
    When the owner archives it
    Then it is archived at once, without waiting for the two Sprints
    When the owner reopens an archived Card
    Then it is out of the archive and back on the screens
    And an archived Action that repeats stays archived: it cannot be reopened
    When a live Card appears under an archived branch
    Then that branch is out of the archive, and what was archived on its own stays archived

  Scenario: CD-REPEAT-026 — A repeat series reads as one series
    Given a repeating Action finished twice, so the series holds three Cards
    Then all three name the same series, and a Card that was never copied names itself
    And each finished one is titled "[🔄2, live #7]": its place in the series, then the open one
      (REPEAT_MARKER)
    And the open one is titled plainly, which is what says it is the one to work with
    And a Card that does not repeat is never titled with a marker
    And an archived one carries "[📦]" after its place in the series (ARCHIVE_MARKER)
    When the series has ended and no open one is left
    Then the last finished one is titled "[🔄3]" (REPEAT_MARKER_ENDED)

  Scenario: CD-HARDTIME-033 — Schedule supplies timing without separate repeat controls
    Given an Action with a Schedule written in plain words
    When the Scheduler compiles it
    Then its appointment is calculated from the validated rule
    And a recurring appointment opens its successor when the Action is completed
    And a one-time appointment opens no successor
    And removing Schedule removes its appointment

  Scenario: CD-REPEAT-032 — A repeating Action says its series was already done today
    Given a repeating Action finished earlier today, and the open one that took its place
    Then both are titled "[🔄✓]" (REPEAT_TODAY_MARKER), on a board, on the Card screen and in
      a citation alike
    And today is the owner's own calendar day, in the workspace timezone
    And the mark says nothing more than that: the open one is still open, and finishing it
      again today is allowed
    And a series whose last completion was yesterday carries no such mark, and neither does an
      Action that never repeated
    And it is the one mark ai_cards does not carry, SQLite having no way to work out the
      owner's day

  Scenario: CD-ARCHIVE-027 — An archived Card opens, and reads as archived
    Given an archived Card, cited in one of Safwa's answers or listed under its Goal
    When the owner taps it, or Safwa opens it
    Then it opens on a screen that says it is archived, offering nothing that would edit it
    And an Action that does not repeat offers Reopen, which takes it out of the archive
    And an Action that repeats offers no Reopen, by CD-ARCHIVE-024
    And a Goal and a Subgoal offer none: they leave the archive when a Card under them is reopened
    And deleting it is offered, and deletes it

  Scenario: CD-DELETE-025 — Deleting a Card is the whole branch, or that Card on its own
    Given a Goal with Subgoals and Actions under it
    When the owner asks for the Goal to be deleted
    Then they are offered both: the branch, and the Goal alone
    And deleting the branch leaves nothing of it, open or closed, archived or not
    And deleting the Goal alone keeps what was under it: a Subgoal becomes a Goal, because a
      Subgoal cannot stand without one, and an Action is left under no one
    And a Card with nothing under it is deleted without the choice, the two being the same
    And a Check that was on a deleted Card is deleted with it, answered or Pending
    And a Check that carries a Value stays instead, with no Card
    And the Values and Tags a deleted Card carried lose the link and nothing else
    And its Sprint commitments and its history go too
    And archiving is never substituted for it
    And Safwa only ever asks for the branch, and Cards calls that destructive, so a proposal
      to do it is confirmed a second time

  Scenario: CD-CONTEXT-028 — The critical Cards Safwa is handed are the ones still to do
    Given critical Cards, some of them Done and some still open
    Then only the ones still open are among the ones Safwa is handed, chosen as VL-READ-003 says
    And each of them says what kind it is and which stage it is in now
    And a Card that is not critical is not among them at all: Safwa looks those up when it needs
      them

  Scenario: CD-VIEW-031 — A Card opens compact, and full editing is one button away
    Given a Card the owner opens from anywhere
    Then the screen says what it is, where it stands, its note and what it costs, and on a Goal
      the Values it carries
    And the buttons are the ones an ordinary day needs: finishing it, moving it between stages,
      and reaching its Checks, its children and its parent
    And Backlog, Sprint and Today list each Action as one full-width button that opens it
    And an open Action has two buttons at the top for moving it to the other live stages,
      in both compact and full editing
    And "✏️ Full editing" opens the same Card with every control it has, and "🗜 Compact" is
      how the owner comes back
    And an edit made in one of the two redraws the Card in that same one
    And an archived Card opens in full, having no control to put away

  Scenario: CD-BLOCKED-034 — After an Action is blocked, Safwa asks whether to set a Reminder
    Given an Action is saved blocked — marked so, or created so — by a proposal or by hand
    When the chat is free
    Then the Advisor is asked once to offer a Reminder for coming back to it, naming the Action and its reason as they are now
    And the Advisor is told, in its standing instructions, that such questions are switched off in the Profile
    And two Actions blocked before it is said make one question about both
    And an Action unblocked, finished, archived or deleted before then is left out, and a question with nothing in it is not asked
    But renaming the Action or rewording its reason while it stays blocked asks nothing
    And the owner's reply is an ordinary turn: the hook reads neither it nor their silence

  Scenario: CD-EMPTY-035 — A Goal or Subgoal left without an Action is asked about each morning
    Given a Goal or Subgoal older than 24 hours (EMPTY_PARENT_GRACE_DAYS = 1) with no Action anywhere under it, a finished or an archived one counted
    When the Profile's Morning time passes, 09:00 unless the owner moved it (MORNING_TIME_DEFAULT = "09:00"), and the chat is free
    Then the Advisor is asked once, about all of them together, to put both ways forward for each in one message: plan its Actions now, or create one Action to plan them later
    And the owner's choice is awaited: nothing is created before their answer
    And one younger than that, archived, or given an Action before the question is said is left out; with none left, nothing is asked
    And the same one still without an Action is asked about again the next morning
    And a check that fires again before the question is said adds no second one

  Scenario: CD-TODAY-036 — A day holding more than it is meant to is asked about
    Given Effort Points are on in the Profile
    Given an Action enters Today — created there, moved there, or opened there as the next instance of a finished repeating one — by a proposal the owner saved or by hand
    When the effort in Today, the open Actions there and the Actions finished that local day together, is over 15 EP (TODAY_CAPACITY_EP = 15) and the chat is free
    Then the Advisor is asked once to say what the day holds against what it is meant to, naming each open Action still in Today with its effort, and to ask which to move back to Sprint
    And nothing is moved before the owner's answer
    And the effort is summed when the question is about to be said: a day at 15 EP or under by then asks nothing, and exactly 15 EP is not over
    And two Actions entering Today before it is said make one question
    And an Action that stays in Today, renamed or re-estimated there, is not what is checked: only one entering Today is

  Scenario: CD-REST-037 — A day planned without rest while the Sprint holds some is brought up
    Given a Sprint runs, and an open Action of the Rest category is in the Sprint or in Today
    When the Profile's Morning time passes and the chat is free
    Then, with no open Rest Action in Today and none finished that day, the Advisor is asked once, in one message naming the Sprint's open Rest Actions
    And it is asked to suggest taking one into Today, rest that is planned being under the owner's control where a need that is not is not; nothing is moved before the owner's answer
    And the list is read when the question is about to be said: with a Rest Action in Today by then, or one finished that day, nothing is asked
    And with no open Rest Action in the Sprint, or no Sprint running, the morning asks nothing
    And a day is the workspace's local day

  Scenario: CD-STALE-038 — An Action that stands in Today morning after morning is brought up
    Given an open Action is in Today when the Profile's Morning time passes
    Then that local day is written down for it as a morning it was chosen — moved there that day or left there across midnight alike — whether or not the question below is switched off
    When it has been found there on three mornings in a row (TODAY_STALE_DAYS = 3) and the chat is free
    Then the Advisor is asked once, in one message naming each such Action with its count of mornings, whether it is too big, blocked or not wanted, and what to do with it; nothing is changed before the owner's answer
    And it is asked again on the sixth morning in a row, and not on the fourth or the fifth; a morning it is not in Today starts the count over
    And the next instance of a finished repeating Action is another Action, with mornings of its own
    And the list is read when the question is about to be said: one that has left Today by then is left out
    And a day is the workspace's local day

  Scenario: CD-TIME-039 — An Action may carry the minutes it took
    Given an Action "Write the report", and Time tracking on in the Profile
    When the owner records 331 minutes on it, typed as "331", "5:31" or "5h 31m" under "⌛ Time spent" in its full editing
    Then the Card carries 331 minutes, and its screen shows "5h 31m", compact and full alike; a whole hour shows as "2h", and less than an hour as "45m"
    And Safwa may propose the time on an existing Action, or with finishing it, in one proposal, whether Time tracking is on or off
    And a Card that carries a time shows it whether Time tracking is on or off
    And a new Card is not written with a time
    And a whole number of minutes from 1 through 1440 is accepted (TRACKED_MINS_MAX = 1440), and anything else is refused, the Card keeping what it had
    And "off" typed on the screen, or a proposal sending none, takes the time away
    And a time on a Goal or a Subgoal is refused
    When a repeating Action carrying a time is finished
    Then the Action that takes its place carries none
    And reopening an Action keeps its time, and an archived one's time cannot be changed

  Scenario: CD-TIME-040 — A Goal shows the time of the Actions under it
    Given a Goal with two Actions under it, of 30 and 45 minutes, and a third with no time
    Then the Goal shows 1h 15m, and so does a Subgoal between them for its own branch
    And an archived Action's time still counts
    And a Goal with no time on any Action under it shows none

  Scenario: CD-TIME-041 — After an Action is finished without its time, Safwa asks how long it took
    Given Time tracking is on in the Profile, and so is the Time tracking reminder
    When an Action with no time is finished — by hand or by a proposal the owner saved — and the chat is free
    Then the Advisor is asked once to ask how long it took and, if the owner says, to record it on that Action
    And two Actions finished before it is said make one question about both
    And an Action reopened, archived, deleted or given a time before then is left out, and a question with nothing in it is not asked
    And an Action finished with its time in the same proposal asks nothing
    And for a repeating Action the question is about the instance that was finished, not the open one after it, and its time is the one change Safwa may propose on that finished instance
    But with Time tracking off, or the reminder switched off, nothing is asked, and a question not yet said is not said

  Scenario: CD-CLOSE-042 — Finished Actions prompt a choice about their open parents
    Given a Goal or Subgoal with Actions under it and Goal completion follow-up switched on
    When its last open Action is finished by hand or a saved proposal and the chat is free
    Then the Advisor is asked once whether to close that Goal or Subgoal too or create a new Action under it
    And all eligible parents, including ancestors, are named together with their IDs and titles
    And nothing is closed or created without the owner's answer
    And partially finished branches and repeating Actions with an open successor ask nothing
    And a parent closed, archived, deleted or given an open Action before delivery is left out
    And a parent with no Actions asks nothing
    And rolling back completion asks nothing
    And switching the hook off prevents new questions and suppresses pending ones

  Scenario: CD-DEADLINE-043 — A Goal or a Subgoal has a Deadline where an Action has a Schedule
    Given a Goal or a Subgoal, written by hand, open on its screen, or proposed
    Then it offers "⏰ Deadline" where an Action offers "⏱ Schedule", and the value is kept as its Schedule
    And the Scheduler reads it as one date, with a time only when one is given, and asks about text that repeats
    And its screen and its draft show the date it was read as
    And it never blocks Done, adds no planned executions, is not reported by get_scheduled and is not compared with the Actions under it
    And in a list a Card with a Deadline comes before one without, the sooner one first
