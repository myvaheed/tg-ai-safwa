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

  Scenario: CD-FIELD-007 — Effort, repeat, categories, energy and Blocked belong to an Action alone
    Given a Goal is being written, by hand or as a proposal
    When effort, repeat, a category, an energy type, or Blocked is set on it
    Then the Goal is saved without them, rather than refused
    And the screens never offer those controls for a Goal or a Subgoal
    And the same holds for a Subgoal, and for a change to one that already exists
    And a proposed change that was nothing but those fields is refused instead of saved empty

  Scenario: CD-EFFORT-008 — An Action has to say what it costs, on the one scale
    Given an Action is being written
    When it is saved with no effort, or with a number off the scale
    Then it is refused (EFFORT_POINTS = 0.5, 1, 2, 3, 5, 8, 13)
    And the Save button is not offered while the draft still has no effort
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

  Scenario: CD-STAGE-015 — A Goal is Done only when everything under it is finished
    Given a Goal whose children have all been finished
    Then the Goal shows Done
    And a Goal with one Done Action and one live Action shows the live one's stage
    And a Subgoal with nothing in it holds the Goal above it in Backlog
    And a Goal with nothing under it never shows Done

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

  Scenario: CD-HARDTIME-033 — A Hard Time is when a Card must happen, and it says what fixes it
    Given an Action "Call the clinic"
    When it is given a Hard Time, typed as "Mon Wed 09:00" or proposed as "every Monday and
      Wednesday at nine"
    Then the Card carries that schedule, worked out the way a Reminder's is, and its next
      occurrence
    And a description may say what fixes the time, and it goes when the Hard Time is removed
    And the review screen shows the schedule in words before Save
    And a phrase that fixes no time is refused, and the refusal says what is missing
    And in a list a Card with a Hard Time comes before one without, the sooner one first
    When a repeating Action with a Hard Time every day at 09:00 is finished
    Then the Action that takes its place is due at the next 09:00
    And a Hard Time that was one moment does not carry to the next Action

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
