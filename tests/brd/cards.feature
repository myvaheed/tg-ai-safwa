Feature: Cards
  A Card is one thing the owner means to do. It is a Goal, an Idea or an Action, and those three
  form a strict tree: a Goal at the root, Ideas under it, Actions at the bottom doing the work.
  What a Card may carry depends on which of the three it is.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a workspace where a Card can be written by the owner or proposed by Safwa

  Scenario: CD-KIND-001 — A Card is a Goal, an Idea or an Action, and it stays the one it was created as
    Given a Card exists as an Idea
    When the owner or Safwa tries to make it an Action
    Then the change is refused and the Card is still an Idea
    And the only way to have an Action instead is to create one

  Scenario: CD-TREE-002 — A Goal is always root-level
    Given the owner has a Goal "Health"
    When a Goal is proposed with a parent, or "Health" is moved under another Card
    Then it is refused, and the refusal says a Goal is always root-level
    And nothing about the Goal changes

  Scenario: CD-TREE-003 — An Idea belongs to a Goal, or to no one
    Given a Goal "Health" and an Idea "Sleep better"
    When "Sleep better" is placed under "Health"
    Then it is placed there
    When another Idea is placed under "Sleep better"
    Then it is refused, and the refusal says an Idea may only be placed under a Goal
    And an Idea under no parent at all is a Card in good standing

  Scenario: CD-TREE-004 — An Action belongs to a Goal, an Idea or no one, and nothing belongs to an Action
    Given a Goal, an Idea under it, and an Action "Buy a pillow"
    When "Buy a pillow" is placed under the Goal, then under the Idea, then under nothing
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
    And the screens never offer those controls for a Goal or an Idea
    And the same holds for an Idea, and for a change to one that already exists
    And a proposed change that was nothing but those fields is refused instead of saved empty

  Scenario: CD-EFFORT-008 — An Action has to say how big it is, on the one scale
    Given an Action is being written
    When it is saved with no effort, or with a number off the scale
    Then it is refused (EFFORT_POINTS = 1, 2, 3, 5, 8, 13)
    And the Save button is not offered while the draft still has no effort

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
    When a Card is written straight into Done or Cancelled
    Then it is refused: a new Card starts in Backlog, Sprint or Today

  Scenario: CD-LINK-012 — A Card is created with all of its Values, Tags and Checks, or the Card is not created at all
    Given a Card is written with the Value "Health", the Tag "home" and the Check "Slept 7 hours"
    When the Check has been archived, or any one of the three no longer exists
    Then no Card appears, and neither do the two links that were fine
    And the owner is told which one could not be linked
    And nothing is left half-written for them to clean up

  Scenario: CD-STAGE-013 — Only an Action has a stage
    Given a Goal, an Idea under it, and an Action under the Idea
    When the owner moves the Action to Today
    Then it is in Today
    When anything tries to move the Goal or the Idea
    Then it is refused before anything is written, in a proposal and on a screen alike
    And no screen offers a Goal or an Idea a stage control

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
    When at least one of them is Done
    Then the Goal shows Done
    When every one of them was Cancelled
    Then the Goal shows Cancelled
    And a Goal with one Done Action and one live Action shows the live one's stage
    And an Idea with nothing in it holds the Goal above it in Backlog
    And a Goal with nothing under it never shows Done or Cancelled

  Scenario: CD-STAGE-016 — Done and Cancelled come from finishing, not from moving
    Given an Action in Today
    When anything tries to move it straight to Done or Cancelled
    Then it is refused, in a proposal and on a screen alike
    And finishing it settles its Checks, its Sprint result and its repeat in the same act

  Scenario: CD-STAGE-017 — Reopening an Action undoes what closing it did
    Given an Action the owner finished as Done, so it has a completion time
    When the owner reopens it
    Then the completion and cancellation times are cleared
    And the Checks it was closed on are given back to it, by CH-REOPEN-012
    And the Goal above it shows a live stage again
    Given instead an Action that repeats and has been finished
    When anything tries to reopen it
    Then it is refused

  Scenario: CD-BLOCKED-018 — Only an Action can be marked blocked
    Given a Goal, an Idea and an Action
    When the Action is marked blocked, with a reason
    Then it is blocked, and the reason is the words that were given
    When anything tries to mark the Goal or the Idea blocked
    Then it is refused before anything is written, in a proposal and on a screen alike
    And no screen offers a Goal or an Idea a Blocked control

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
    And an Idea between them shows the total of its own branch
    And a Goal and an Idea still refuse an effort set by hand
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
    Then it is refused: only a Card that is Done or Cancelled may be archived
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

  Scenario: CD-ARCHIVE-027 — An archived Card opens, and reads as archived
    Given an archived Card, cited in one of Safwa's answers or listed under its Goal
    When the owner taps it, or Safwa opens it
    Then it opens on a screen that says it is archived, offering nothing that would edit it
    And an Action that does not repeat offers Reopen, which takes it out of the archive
    And an Action that repeats offers no Reopen, by CD-ARCHIVE-024
    And a Goal and an Idea offer none: they leave the archive when a Card under them is reopened
    And deleting it is offered, and deletes it

  Scenario: CD-DELETE-025 — Deleting a Card deletes everything under it
    Given a Goal with Ideas and Actions under it
    When the owner or Safwa asks for the Goal to be deleted
    Then the Goal and everything under it is gone, open or closed, archived or not
    And a Check that was on them is deleted with them, answered or Pending
    And a Check that carries a Value stays instead, with no Card
    And the Values and Tags they carried lose the link and nothing else
    And their Sprint commitments and their history go too
    And archiving is never substituted for it
    And Cards calls this destructive, so a proposal to do it is confirmed a second time
