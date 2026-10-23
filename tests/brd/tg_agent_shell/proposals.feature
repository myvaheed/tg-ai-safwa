Feature: Proposals
  A proposal is how Safwa changes anything: the model never writes, it proposes, and the owner
  saves or discards. What a proposal may not reach is as much a rule as what it writes.

  Background:
    Given a workspace where every change Safwa makes is a proposal the owner decides on

  Scenario: PR-TARGET-001 — An archived item is not changed automatically
    Given an item the owner has archived
    When Safwa proposes a change to it
    Then it is refused, and the refusal says an archived item is not changed automatically
    And Safwa ends its answer citing it, for the owner to open and change by hand
    When Safwa proposes a change to an id that matches nothing
    Then it is refused for that reason instead, and Safwa is sent to find the id

  Scenario: PR-WRITE-002 — Safwa proposes, and only the owner writes
    Given the owner asks Safwa to change something on their workspace
    When Safwa decides what should be done
    Then nothing is written: a review screen appears with the proposal on it
    And what it proposes happens only once the owner has saved it
    And the tool that proposed it belongs to a subagent, never to Safwa's own voice
    And the only change that is saved without that screen is one PR-AUTO-024 covers, which is
      proposed and stored the same way before it is saved

  Scenario: PR-SCREEN-003 — A review screen shows everything the proposal would do, behind Save and Discard
    Given a review screen is open on a proposal
    When the owner looks at it
    Then its heading says what Save does and to which item: "➕ Create Action", "✏️ Update Tag",
      "📦 Archive Check" or "🗑 Delete Reminder"
    And it carries Save, named for what it does — "✅ Create", "✅ Update", "✅ Archive" or
      "✅ Delete" — and "🗑 Discard" beside it, with "✏️ Edit" above them only when PR-EDIT-033
      offers a field
    When the proposal creates the item, or archives or deletes it
    Then the screen shows the item as it will be, or as it is, all in plain words
    And a field that is empty, or that a new item takes by default, is not shown
    When the proposal edits the item
    Then the screen shows what names the item and each field the edit changes, once and with no
      arrow
    And what goes is struck through and what comes is bold
    And a value of at most 3 words on both sides reads whole, the old struck and the new bold
      (SHORT_VALUE_WORDS = 3)
    And words that lose more than half of their characters read as the new words alone, bold
      (REPLACED_SHARE = 0.5)
    And a field emptied reads as its old words, struck
    And a list strikes each item taken off and bolds each item added
    And a field longer than 200 characters folds into a quote the owner opens
      (LONG_TEXT_CHARS = 200)
    When the proposal holds more than one edit
    Then every one of them is on that same screen, each field marked from before the first to after
      the last, still behind one Save and one Discard
    When the owner decides a proposal no request is waiting on
    Then its screen becomes one line: the decision and what the proposal does, as
      "✅ Saved — Edit Tag “Family” (Description: Relationships and home)"

  Scenario: PR-SCREEN-004 — A change its own feature calls destructive asks once more
    Given a proposal to make a change the feature that owns the item declares destructive
    When the owner presses Save
    Then nothing is written yet: one more screen asks to confirm, saying what goes with it
    And it is written only after the owner presses that confirmation
    And a proposal whose feature declares no such change never asks twice

  Scenario: PR-QUEUE-005 — Everything Safwa proposes for one item at once is one screen
    Given the owner asks for something that needs the workspace changed
    When Safwa proposes an edit to one item, whether it sets one field or five
    Then it becomes one proposal with one review screen
    When one response carries several calls in a row for one item that already exists — an
      edit and a link, say
    Then they are one proposal with one review screen, marking the item from before the first of
      them to after the last by PR-SCREEN-003
    And Save saves all of them or none of them, and Discard discards all of them
    When a proposal for another item stands between two calls for the same item
    Then they stay separate proposals, in the order Safwa made them

  Scenario: PR-QUEUE-006 — Several proposals from one request are reviewed one at a time, in the order Safwa made them
    Given one request from the owner needs Safwa to propose three times
    When the proposals are put together
    Then there are three review screens, not one screen listing three items
    And they are queued in the order Safwa made them, not sorted, and grouped only by PR-QUEUE-005
    And each heading ends with its place in the queue, "✏️ Update Action · 2/3"
    And a proposal that is alone in its queue carries no place

  Scenario: PR-QUEUE-007 — Only the front of the queue is on screen, and Safwa answers after the last one
    Given three proposals from one request are queued
    When the owner saves the one on screen
    Then nothing is written yet: the Save is kept, the screen says "☑️ Will be saved", and the next one in the queue takes its place on screen
    And Safwa says nothing about the request while any of them is still undecided
    And once the last one is decided, every kept Save is written by PR-SAVE-031, and Safwa is given every decision at once and then answers

  Scenario: PR-QUEUE-008 — Saving one proposal does not spoil the ones behind it
    Given Safwa proposed three edits to the same Card with another Card's edit between each two,
      so all three are queued
    When the owner saves all three
    Then each is written on top of the one before it, in the order Safwa made them
    And the screens of the second and the third show the Card as it is before the queue is written

  Scenario: PR-SAVE-009 — Save writes through the same operations the manual screens use
    Given a proposal to move an Action to Done
    When the owner saves it
    Then the Action moves exactly as it would if the owner had pressed Done on its own screen
    And everything that follows from that follows too, the Goals above it and its Sprint included
    And the proposal is recorded as saved, and its screen can no longer be acted on
    When a proposal holds more than one edit
    Then they are applied in the order the review screen listed them

  Scenario: PR-SAVE-010 — Discard writes nothing and ends the request's queue
    Given a proposal is on screen, one from the same request was saved before it, and two more are queued behind it
    When the owner discards it
    Then nothing about that item changed
    And the proposal is recorded as discarded, and its screen can no longer be acted on
    And the two behind it are never shown: each is recorded as discarded, with the reason that the owner discarded an earlier proposal of the request
    And the one saved before it is written by PR-SAVE-031
    And Safwa is given every decision at once and says which were not shown; it proposes them again only if the owner asks

  Scenario: PR-SAVE-031 — A request's saves are written together, once its queue ends
    Given the owner saved some proposals of one request and its queue has ended: the last one decided, one discarded, a message written instead, or no answer in time
    When the saves are written
    Then they are written in the order Safwa made them, in one transaction committed once
    And they are written all or none: when one fails, nothing of them is written and the hooks hear of none of them
    And one is refused, and with it all of them, when the workspace moved on after its screen was shown
    And the hooks hear of all of them once, after that commit
    And saves kept when Safwa restarts before the queue ends are lost with the queue
    And a proposal no request is waiting on is written at once by its Save

  Scenario: PR-RESULT-011 — After each decision Safwa is told what became of that proposal
    Given the owner saved one proposal and discarded another from the same request
    When Safwa carries on with that request
    Then for each one it is told the decision, which item it was and what it would have done
    And for the saved one it is told not to propose it again
    And for the discarded one it is told that it did not happen, not to propose it again, and to
      go on only with the parts of the request it has not proposed yet
    When the same session sends the same call for either of them again
    Then nothing is prepared and no screen is shown: the call is told the owner already saved or
      discarded that change, and not to propose it again
    And each such call counts as one of the 5 tries (MAX_REPAIR_ROUNDS = 5)
    When the subagent hands its answer back
    Then Safwa is told to route again only for a part of the request no receipt line covers

  Scenario: PR-STALE-012 — A proposal is refused once the workspace has moved on without it
    Given a proposal is on screen and its review has not expired by PR-EXPIRE-029
    When the Sprint reaches its planned end and closes itself
    And the owner presses Save before the review closes
    Then nothing of it is written, and when it is in a queue, nothing of that queue is written by PR-SAVE-031
    And the owner is told the workspace has moved on since Safwa proposed this, and it has to be
      proposed again
    And the screen cannot be acted on any more

  Scenario: PR-STALE-013 — A proposal lives for one running process
    Given a proposal was made in the current running process and its review is still open
    And the workspace has not moved on since it was proposed
    When the owner presses Save, however long that process has stayed alive
    Then the proposal is applied normally
    And the review is over
    But a review nobody answers closes after 30 minutes by PR-EXPIRE-029, and its old Save writes nothing
    When Safwa restarts instead before the owner answers another proposal
    Then its review, its queue and its buttons are all gone, and the Saves kept in that queue with them: nothing of them is written
    And pressing the old Save button writes nothing and says the screen has to be reopened

  Scenario: PR-FAIL-014 — A proposal that fails while it is being saved is tried once more
    Given three proposals from one request are saved and the second one cannot be applied
    When the queue ends and the saves are written by PR-SAVE-031
    Then the second is recorded as failed and nothing was written for it
    And the first and the third are not written either, and are recorded as failed because another one of the request failed
    And Safwa is told to fix the second and try it once more, and to try the first and the third once more unchanged
    When the same failure happens to a proposal no request is waiting on
    Then the proposal stays as it was and its screen comes back with the reason on it

  Scenario: PR-REPAIR-015 — A tool call Safwa could not turn into a proposal comes back as an error, not a screen
    Given one request where two of Safwa's three calls are well formed and the third is not
    When the proposals are put together
    Then the two well-formed ones are queued for review
    And the third opens no screen at all
    And Safwa is told what was wrong with it and what to send instead
    And Safwa is told its other calls were not cancelled and are waiting for the owner

  Scenario: PR-REPAIR-016 — After five tries Safwa stops rather than keep guessing
    Given Safwa keeps sending a call it cannot get right (MAX_REPAIR_ROUNDS = 5)
    When the fifth attempt fails as well
    Then Safwa stops trying and says it could not prepare that one
    And it says that nothing unfinished was applied
    And whatever it did get right in the same request is untouched

  Scenario: PR-INTERRUPT-017 — Writing instead of answering ends the review
    Given a review screen is in the chat and two more proposals from that request are queued behind
      it
    When the owner writes a new message instead of pressing Save or Discard
    Then none of the three is written
    And each of them is recorded as discarded
    And the screen loses its buttons and becomes a plain account of what the request did
    And the message they wrote is answered as their next request

  Scenario: PR-INTERRUPT-018 — What was already saved is still reported as saved
    Given three proposals from one request, the first of them already saved
    When the owner writes a message instead of deciding the second
    Then the first one is written by PR-SAVE-031, and the second and the third are recorded as discarded
    And the account left in place of the screen names all three, the saved one included

  Scenario: PR-EXPIRE-029 — A review nobody answers is closed after 30 minutes
    Given a review screen is in the chat and one more proposal from that request is queued behind it
    And a Reminder came due while the screen stood
    When 30 minutes pass from the moment the screen was shown without Save, Discard or a message
      from the owner (PROPOSAL_REVIEW_MINUTES)
    Then neither proposal is written, and each is recorded as expired, not as discarded
    And what the owner had already saved in that request is written by PR-SAVE-031 and reported as saved
    And the screen loses its buttons and becomes an account that the system closed the request
    And pressing its old Save writes nothing and cannot resume the closed request
    And the owner's next message starts a new request; the closed one is not resumed and nothing
      in it is proposed again
    And the Reminder is delivered after the screen is closed, on the same poll
    And time spent queued does not count: the proposal behind the screen is on the clock only
      once it is shown

  Scenario: PR-AUTO-024 — Autoapproval saves a change with no screen when it is exactly what was asked for
    Given autoapproval is on in the feature toggles
    And the owner asked for a change to an item they already have
    When Safwa proposes that change, and both the operation and every field it sets are ones
      autoapproval is allowed to save
    Then the proposal is read against the owner's own words, and saved only if it is exactly what
      they asked for
    And it is saved with no screen ever shown, stored and recorded the way one the owner saved by
      hand is
    And Safwa is told autoapproval saved this one and that the owner decided nothing
    And a proposal holding several changes to one item is read in one review, all of them
      together, and saved whole or shown whole

  Scenario: PR-AUTO-025 — Autoapproval never covers a new item, and never an unlisted change
    Given autoapproval is on in the feature toggles
    When Safwa proposes to create an item the owner does not have yet
    Then its review screen is shown, and the proposal is never read against their words
    When Safwa proposes an operation that is not on the list, or one that sets a field outside what
      that operation may set
    Then that one is shown as well, and it is not read against their words either
    When a proposal holds several changes to one item and any one of them is not on the list
    Then the whole proposal is shown, and none of them is read against their words

  Scenario: PR-AUTO-026 — Doubt leaves the proposal exactly as it was
    Given autoapproval read a proposal that could be read in more than one way
    When it decides the owner should see it
    Then the proposal stays exactly as it was prepared, and its review screen is shown
    When autoapproval cannot reach a decision at all
    Then the review screen is shown for that reason instead, and nothing about the proposal changed

  Scenario: PR-AUTO-027 — In a queue, autoapproval reads one proposal at a time, at the front
    Given one request from the owner becomes three proposals, queued in the order Safwa made them
    When autoapproval saves the first and the second has to be shown
    Then the second is on screen, and the third has not been read by autoapproval at all
    When the owner decides the one on screen
    Then the third is read then, and autoapproval may still save it with no screen of its own

  Scenario: PR-PLAN-028 — A subagent says what it will change before its calls become proposals
    Given the plan check is on in the feature toggles
    And a subagent is sending the calls that would become proposals
    When the response carrying them has no text of its own
    Then none of those calls is prepared, and each comes back refused as plan_required, by
      AG-HOOK-046
    And the subagent is told to write what it will change, in order, as the text of that
      response and send the calls again
    When it sends them again under such a text
    Then they are prepared as they would have been
    And it is the response carrying the calls that is held to this, whichever response of
      the session that is: a read before it needs no text
    And that text is the subagent's own working record, kept in its session and not in
      what the owner reads
    When the plan check is off in the feature toggles
    Then a response carrying calls and no text is prepared as it would have been

  Scenario: PR-SIMILAR-030 — A screen that creates an item lists the open items of its type most like it
    When a proposal creates a Card, a Check, a Value, a Tag, a Request or a Reminder
    Then its screen lists, under "⚠️ Similar items already exist" right below its heading, up to 3
      open items of the same type whose words are closest in meaning to the new one's, closest
      first (SIMILAR_ITEMS_SHOWN = 3)
    And each is named by PR-CITE-032, its title whole
    And a Card is compared with every open Card, a Goal, a Subgoal or an Action alike
    And an item is listed only when it is at least 0.80 alike (TEXT_MODEL.alike = 0.80)
    And an open item is a Card not Done and not archived, a Check not answered, any Value, Tag
      or Request, and a Reminder the owner set
    And the words compared are a Card's or a Check's title, a Value's, a Tag's or a Request's
      name, and a Reminder's instruction
    When nothing is that alike, or the proposal changes or deletes an item
    Then the screen has no such list
    When the text model has not finished loading, or fails
    Then the screen comes without the list

  Scenario: PR-CITE-032 — A review screen names the items it cites and links none
    Given a review screen whose words cite an item: a Note or a description it proposes
    When it is drawn
    Then the item reads as it does in Safwa's answers, with its emoji and its whole title
    And it is not a link
    And a citation of an item that no longer exists keeps its words

  Scenario: PR-EDIT-033 — Edit offers only the fields the proposal sets, each with its item's own control
    Given Safwa proposes to give the Action "Run" an estimate of 5 Effort Points
    When its review screen is drawn
    Then "✏️ Edit" stands above Save and Discard
    When the owner presses it
    Then the review stays in place, and under it there is one button, "🔢 Effort", then "↩️ Back"
    When the owner presses "🔢 Effort"
    Then the same choices the Action's own screen offers for Effort take the review's place, 5 ticked
    And a field offers words to type, one choice to pick or several to tick, the way the item's
      own screen offers it, and offers choices beside "✏️ Other" for words where that screen does
    And a field the proposal does not set has no button, and neither has a field its feature does
      not let the owner set by hand
    And a value the check of the proposal leaves out of what Save writes has no button, as Effort
      Points while they are off
    When Safwa proposes instead to link the Value "Health" to an Action that already carries "Calm"
    Then Edit offers "💎 Values", listing every Value but "Calm", with "Health" ticked
    And a proposal to unlink lists only the Values the Action carries
    And whatever the proposal, an item is ticked when the proposal names it
    And a field can stand for what the proposal does to the item, not only for a value it sets:
      picking another choice changes what Save will do
    When nothing the proposal sets is a field its feature offers — an archive, a delete, or an
      item whose feature offers none
    Then its screen has no "✏️ Edit"
    And a field that two changes of one proposal both set has no button

  Scenario: PR-EDIT-034 — The owner's value is checked the way Safwa's own would be
    Given the owner pressed a field under Edit
    When they pick a value, or type one
    Then the proposal is checked again as if Safwa had sent it with that value, against the
      workspace as it is now
    And while it is checked, the chat shows Safwa typing
    And what was read from values the owner left as they were is not read again
    And the review comes back in place of the field, marked by PR-SCREEN-003 from the item as it is
      to the item as Save would leave it
    And under its heading it names each field the owner set, as "✍️ Edited by you: 🔢 Effort"
    And nothing is written before the owner presses Save
    When the owner's value means the proposal no longer sets another field
    Then that field's button goes, and what the owner set there goes with it
    When the check refuses the value
    Then the proposal stays as it was, and the reason is on the screen
    When the field takes several items
    Then each tap only moves a tick, and "↩️ Back" carries what is ticked into the proposal
    When the owner presses "↩️ Back" on a field that takes words or one choice
    Then the field is left as it was and Edit's buttons come back
    When the owner presses "↩️ Back" under Edit
    Then the review comes back with Save and Discard

  Scenario: PR-EDIT-035 — Editing a proposal keeps its review open
    Given the owner is editing a proposal under Edit
    Then every screen of it is drawn in place of the review, and the review stays the last
      message in the chat
    When they send the words a field asks for
    Then those words are the field's value, they leave the chat, and the review is not interrupted
    When they write anything while a field's choices are on screen
    Then the review is interrupted by PR-INTERRUPT-017
    And a review being edited closes after 30 minutes without an answer by PR-EXPIRE-029, counted
      from when the review was last drawn (PROPOSAL_REVIEW_MINUTES)
    When the review closed while a field waited for words, or Safwa restarted
    Then the words the owner sends next are their next request, not that field's value

  Scenario: PR-EDIT-036 — Safwa is told what the owner changed before saving
    Given Safwa proposed 5 Effort Points for the Action "Run"
    And the owner set them to 3 under Edit and saved the proposal
    When Safwa carries on with the request
    Then it is told the proposal was saved with the owner's change: the field, what it proposed
      and what was saved
    And everything it reads about the saved change says 3, as does the line the chat keeps
    And it is told to report what was saved, and never to propose its own value again
    When the owner sets a field back to what Safwa proposed
    Then nothing about that field is reported as changed
    When the owner discards a proposal they edited
    Then Safwa is told it was discarded, by PR-RESULT-011

  Scenario: PR-SWITCH-037 — A feature may offer to turn a proposal into another one before Save
    Given a feature declares that a proposed change of its item may turn into another call
    When the review screen of a proposal that offers it is drawn
    Then its button stands above Edit, Save and Discard
    And a proposal its feature does not offer it to, or that holds several changes, has no such button
    When the owner presses it
    Then the feature writes the other call, which may read and ask a model and writes nothing
    And that call is checked as Safwa's own would be, while the chat shows Safwa typing
    And what that call carries already read is not read again
    And the same review now holds that call in place of the first, drawn as that item's review
    And nothing is written before the owner presses Save
    When the check refuses the other call
    Then the proposal stays as it was, and the reason is on the screen
    When the owner saves it
    Then the other call is written, and the call Safwa made is told it was saved with what was
      written: that item, its result ids and its fields
    When the owner discards it
    Then nothing is written, and Safwa is told it was discarded, by PR-RESULT-011
