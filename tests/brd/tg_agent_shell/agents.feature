Feature: Agents — the session, the hand-over, and what comes back

  Safwa answers the owner with one voice and more than one session. The part that talks to the
  owner reads everything and changes nothing; the part that changes something is handed the turn,
  reads the same conversation, and hands back a receipt. A session survives being paused on a
  screen, carries one budget however long it takes, and ends when the process does.

  Approved 2026-08-29, and AG-TURN-010, AG-TURN-022 and AG-TURN-023 on 2026-08-30.

  Scenario: AG-ROUTE-001 — A change is written by the part that owns it, and Safwa itself writes none
    Given the owner asks Safwa to change something on their workspace
    When Safwa works on it
    Then the change is prepared by the subagent that owns that area
    And the part answering in the chat has no way to change anything itself, in any area, so a
      change is always someone else's work
    When the owner instead asks a question about anything in their workspace, the Diary included
    Then that same part answers it from its own reading, without handing the turn to anyone

  Scenario: AG-ROUTE-040 — The hand-over carries a name, never a retelling
    Given the owner wrote a request in their own words
    When the turn is handed to a subagent
    Then the subagent is given the name of its area and no retelling of the request
    And it reads the same recent conversation, so the owner's exact words are what it works from

  Scenario: AG-ROUTE-002 — The conversation reaches the subagent as data, not as its own voice
    Given the conversation the subagent is handed
    When it reads it
    Then the newest messages arrive as one block, each tagged with who wrote it: as many as the
      subagent declared, 10 unless it says otherwise (SUBAGENT_HISTORY_LAST_MESSAGES = 10)
    And none of them arrives as if the subagent had said it

  Scenario: AG-ROUTE-003 — A request that names two areas is one request, not two
    Given the owner asks for one thing on their workspace and one thing in their Diary, in a single
      message
    When Safwa works on it
    Then each area is handed to the subagent that owns it, one after the other, within the same
      request
    And the request is not finished until both have been dealt with

  Scenario: AG-ROUTE-004 — The part doing the work cannot hand the work on again
    Given a subagent is doing the work it was handed
    When it looks for a way to hand that work on to another subagent
    Then it has none, so the chain is never more than two deep

  Scenario: AG-ROUTE-005 — Handing the turn over is the only thing Safwa does in that step
    Given Safwa decides to hand the turn to a subagent
    When it tries to do something else in the same step
    Then nothing in that step runs, Safwa is told to hand over on its own, and it tries again

  Scenario: AG-RECEIPT-006 — Only Safwa writes to the chat
    Given a subagent finished the work it was handed and has something to say about it
    When the request ends
    Then its words went back to the part answering in the chat, and reach the chat only inside
      the message that part writes: retold, or printed as they are when the subagent is declared
      so, by AG-RECEIPT-044
    And that part writes the single message the owner reads, keeping the links to any item named

  Scenario: AG-RECEIPT-007 — Work that breaks comes back as a report, not as a silence
    Given a subagent fails part-way through the work it was handed
    When the failure happens
    Then what comes back is a short account of the failure, in place of a result
    And Safwa still answers the owner in the same request, telling them what did not happen

  Scenario: AG-SESSION-008 — Work paused on a screen picks up where it stopped
    Given a subagent prepared a change and its screen is waiting in the chat
    When the owner saves it, minutes or hours later
    Then the subagent carries on from the step it stopped at, with everything it had already worked
      out
    And its result then reaches the part that handed it the work, which answers the owner

  Scenario: AG-SESSION-041 — What it knows about the workspace is read fresh, not replayed
    Given the subagent stopped for the owner
    When it starts again
    Then what it knows about the workspace and the time of day is read fresh, not replayed from when it
      stopped

  Scenario: AG-SESSION-009 — A restart ends every piece of work that was waiting
    Given work was paused on a screen, or was running when the process stopped
    When Safwa starts again
    Then none of it is picked up, and the owner starts from a request they make now
    And no button left in the chat can revive it

  Scenario: AG-TURN-010 — One request at a time, and nothing that arrives during one joins it
    Given Safwa is working on a request
    When the owner writes another message
    Then it does not start a second request
    And it is taken out of the chat, and nothing is done with it
    When the owner sends a recording instead
    Then that is taken out of the chat too, and never transcribed
    When the owner presses a button on any screen
    Then it does nothing while the request is running
    When the owner runs /cancel
    Then the running request is stopped, and that is the one thing they can always do

  Scenario: AG-BUDGET-011 — One request has one tool budget, however many screens it opens
    Given a request that opens several screens before it is finished
    When the owner answers each of them and the work carries on
    Then every step it has taken counts against one budget of 64 (MAX_TOOL_CALLS = 64)
    And the budget is not refilled by a pause, so a request that never settles is stopped

  Scenario: AG-BUDGET-012 — A subagent that takes too long is stopped by the clock
    Given a subagent is working while the owner waits for an answer
    When it has been working for 300 seconds without finishing (SUBAGENT_DEADLINE_SECONDS = 300)
    Then it is stopped, and the owner is told it did not finish
    When instead a screen it opened is waiting for the owner
    Then the time the owner takes to decide does not count against that, however long they take

  Scenario: AG-ANSWER-013 — A step that produces nothing is asked again, and the owner is never left with nothing
    Given a step ends with neither an answer nor anything to do
    When that happens
    Then Safwa is told it stopped without answering, and asked again, up to 5 times
      (MAX_REPAIR_ROUNDS = 5)
    And if it still has nothing, the owner is told so in one line rather than left with silence

  Scenario: AG-ANSWER-014 — A subagent's first step is always the work
    Given a subagent has just been handed a turn
    When it takes its first step
    Then it has to do something, not answer in words — it was handed the turn for the work —
      unless it is declared shown as is, whose words are the work, by AG-RECEIPT-044
    And every step after that is free to be the answer, or the work would never finish

  Scenario: AG-TURN-015 — Safwa speaks unasked only when nothing of the owner's is open
    Given the system has something for Safwa to say without being asked, such as a Reminder coming
      due
    When a request of the owner's is running, or a screen of theirs is waiting for a decision
    Then Safwa says nothing, and what it owes them is still owed
    When nothing of the owner's is open
    Then it is said as one ordinary answer, and only then is it no longer owed
    When the owner writes while Safwa is part-way through saying it
    Then the owner wins, the half-written message is thrown away, and it is still owed

  Scenario: AG-WORDS-016 — Words typed over a screen continue the request that opened it
    Given a subagent proposed a change and its screen is waiting in the chat
    When the owner writes a message instead of pressing Save or Discard
    Then the request that opened that screen carries on and answers those words
    And it is not thrown away and started again from nothing
    And the part answering in the chat is what reads them, before anything is handed anywhere

  Scenario: AG-WORDS-017 — The resumed request is told the whole of what happened
    Given a request that had handed the turn to a subagent and is waiting on its result
    When the owner writes over the screen that subagent opened
    Then the waiting request is given what was proposed, what was refused, what was already saved,
      and what the owner wrote
    And it answers from those four things together, so a half-finished request is not reported as
      done

  Scenario: AG-WORDS-018 — A correction reaches the subagent that wrote the refused proposal
    Given a subagent proposed a Diary entry and the owner refused it by writing "the same, but
      shorter"
    When Safwa hands the turn back to that subagent
    Then the same subagent picks the work up, still holding the entry it had written
    And what it proposes next is that entry corrected, not a new one written from scratch

  Scenario: AG-WORDS-019 — The subagent's own record says the owner refused and wrote instead
    Given a subagent's proposal was refused by words the owner typed
    When it picks the work up again
    Then its own record of the work says both that the proposal was refused and that the owner
      wrote something instead of deciding
    And it does not propose the same thing a second time

  Scenario: AG-WORDS-020 — Unfinished work ends when the request that started it ends
    Given a subagent was interrupted and its work was left unfinished
    When the request that had handed it the turn finally answers the owner, or fails
    Then that unfinished work is ended right there
    And nothing from it can be picked up by a later request

  Scenario: AG-WORDS-021 — Saving finishes the subagent, so asking it again starts it fresh
    Given the owner saved a change a subagent proposed, and that subagent finished and reported back
    When the same request hands the turn to that same subagent again
    Then it starts from nothing, with no memory of the change already saved
    And it works from the conversation, where the saved change is already visible

  Scenario: AG-TURN-022 — While an answer is being written, the chat says so and offers to stop it
    Given the owner asked Safwa something
    When Safwa begins writing the answer
    Then one message stands in the chat saying the answer is being written, offering /cancel as
      something they can tap
    And there is one of those however much the owner sends meanwhile
    When the answer arrives
    Then that message is taken out of the chat
    When the owner cancels instead
    Then it is taken out of the chat the same way, and no answer is written
    And it is never part of the conversation

  Scenario: AG-TURN-023 — Words Safwa could not take out of the chat are answered rather than left hanging
    Given Safwa is working on a request
    When the owner writes, and Telegram refuses to let Safwa take that message out of the chat
    Then the running request is stopped, and what they wrote is answered now

  Scenario: AG-TURN-024 — One lease, and the owner always wins
    Given work Safwa does on its own, with nothing to say to the owner
    When a request of the owner's is being answered
    Then that work does not start, because answering the owner comes first
    And it can never take the turn away from the owner, however long it has been waiting
    When it finishes and gives the turn back
    Then Safwa is free for the next thing

  Scenario: AG-TURN-024 — Work the owner overtook is thrown away rather than saved
    Given background work that started from the conversation as it stood
    When the owner says something before it has stored anything
    Then its result is thrown away, because it no longer covers the whole conversation
    And a later attempt covers the owner's words too

  Scenario: AG-HELPER-025 — A helper runs inside the owner's turn, not beside it
    Given Safwa has called a helper
    Then the owner's turn is still running and no screen is shown
    And text the owner sends meanwhile is not answered while it runs, by AG-TURN-010
    When the owner cancels the answer
    Then the helper stops with the request it belongs to

  Scenario: AG-HELPER-026 — A helper stays offered across a screen the session opened
    Given Safwa was offered a helper
    And it then routed a change that opened a screen
    When the owner saves it and the session resumes
    Then that helper is still on its tool list

  Scenario: AG-READ-027 — A reader asks with one SELECT, over the views it was given
    Given a part of Safwa that reads the workspace to answer a question
    When it asks the database something
    Then it may send one read-only SELECT, or one WITH … SELECT, and nothing else
    And it reaches only the views its own list names, so a view no list names is one that reader
      never learns exists
    And a word like delete inside quoted text is text, and is not something to refuse over

  Scenario: AG-HELPER-028 — A helper that spends its budget without an answer comes back as a failure
    Given a helper reading its way to an answer, under the budget its caller gave it
    When the budget is spent and it has still not ended the session
    Then it comes back as a failure, not as whatever it happened to say last
    And a helper that ends the session before it read anything is refused the same way

  Scenario: AG-CUE-029 — Something Safwa owes the owner survives until it is said
    Given Safwa owes the owner something it was not asked for
    When it cannot be said yet
    Then it stays written down, and the next check offers it again
    And one check says everything owed by then in one turn, as one request, the oldest first
    And what that turn says is written down before it starts, under the turn's own id, and only that is settled once it has landed
    When a turn is started for it and does not reach the chat
    Then it is still owed, and nothing about it was thrown away
    When its arrival in the chat has been registered
    Then no later check says it a second time, and what was said with it is settled with it
    And what was written down once the turn had started is not settled with it: it is said by a later check, as a turn of its own
    And a turn registered in the chat but not settled when Safwa stopped is settled by the next check, without a word, and nothing written down since is settled with it
    And when nothing is owed, no turn is taken at all

  Scenario: AG-POLL-030 — Work on a timer outlives its own failures
    Given work Safwa does on a timer rather than when the owner asks
    When one round of it raises
    Then the failure is written down, and the next round comes at its usual time
    And it does not quietly stop for the rest of the run, leaving that part of Safwa dead
    When Safwa is shutting down
    Then the timer ends with it, because shutting down is not a failure to be survived

  Scenario: AG-TOOL-031 — A feature may answer a tool call instead of letting it run
    Given a feature watching the calls the model makes
    When the model calls a tool and that feature answers with a result of its own
    Then the tool does not run, and what the feature wrote is what the model reads back
    When it answers with nothing
    Then the tool runs as it would have with nobody watching
    When it fails while deciding
    Then the turn ends there, and the call it had not allowed is not run
    And what the owner is told names that feature's watcher and the call it fell over on

  Scenario: AG-TOOL-032 — A feature may read what a tool call produced
    Given a feature watching what the model's calls come back with
    When a tool has run
    Then that feature is given the call and the result it produced
    And what it adds to that result is what the model reads
    When it fails instead
    Then the turn ends there too, and the owner is told which watcher and which call,
      by AG-TOOL-031

  Scenario: AG-TOOL-033 — A read that failed is not also offered something else to do
    Given a read the model made came back as a failure
    Then it is told how to repair that one read, and nothing else is added to the result
    And an offer the read would otherwise have earned waits for a read that worked

  Scenario: AG-TURN-034 — Work after the answer cannot take the answer back
    Given the owner has been answered and the turn has been given back
    When work the application runs after that fails
    Then what they are told names that work, and says their answer stands
    And it never says the request could not be completed
    And the rest of that work still runs

  Scenario: AG-HOOK-035 — Automatic reactions have one explicit and checked connection list
    Given an application declares its features and automatic reactions
    Then each reaction has one unique name and an existing feature that owns it
    And its event and kind of work must be supported together
    And every helper and tool it names must exist at that event boundary
    When the application's policy switches a reaction off
    Then it stays in the catalogue and its condition is not checked
    And its declaration is validated before the application starts, on or off
    And a reaction that runs work of its own is on whatever the policy says; one that hands the agent a helper or a request is the owner's to switch
    And an application with no settings of its own keeps every reaction on
    When an unrelated event occurs
    Then the reaction's condition is not checked

  Scenario: AG-HOOK-036 — An automatic offer grants only its named helper to its own session
    Given a successful read offers one helper to the root session
    Then that helper becomes available before the next model request
    And a different helper cannot be called on the strength of that offer
    When the session resumes after a proposal decision
    Then it still may call the helper it was offered
    When a new session begins
    Then the previous session's offer grants it nothing
    When a hook refuses a call before it runs, naming a helper
    Then the tool does not run, the hook's notice is what the model reads as the call's result, and that helper is granted the same way
    And a refusing check that fails, or whose notice is not words, ends the turn, naming the hook and the call: a refusal that failed is not a pass
    And the refusal stands in a session the helper cannot be granted to

  Scenario: AG-HOOK-037 — A saved change reaches its reaction once, on commit, whoever saved it
    Given a feature records a change beside the transaction that makes it
    When that transaction is committed
    Then the reaction for that kind of change checks it once, from a proposal and from a screen alike
    When the transaction is rolled back, or the proposal that would have made it is discarded
    Then no reaction checks anything
    When the reaction is work of its own
    Then it is done once that commit's facts are handed on, and a change saved meanwhile is handed on without waiting for it
    And its failure is logged and stops nothing

  Scenario: AG-HOOK-038 — A hook keeps one pending request, made current just before it is said
    Given a hook asks the Advisor through what Safwa owes the owner
    Then what is written down is the hook's name and what it refers to, not the words
    And it survives a restart, like anything else Safwa owes
    When the hook fires again before that is said
    Then what it refers to is written down beside the first, and the owner is asked once, about all of it, each thing once
    When the request is next in line and the chat is free
    Then the hook is checked to be still on, what it refers to is read again, and the words are made from what is still there
    And while the chat is busy nothing is read
    And when nothing is still there, nothing is said and nothing stays owed
    And when the words cannot be made, the request stays owed and is tried again
    And when several requests are owed at once, each is worded on its own and they are said in the one turn; one whose words cannot be made stays owed alone
    And what the hook added while the request was being said is owed still, as the next one
    When the owner switches the hook off in the Profile
    Then its pending request is dropped, and switching it back on does not bring it back

  Scenario: AG-HOOK-039 — A check on a schedule runs once per period, and not for the periods it slept through
    Given a hook declares a daily check at a local time of day its subscription names — a Profile field, read by the workspace's clock
    When that time passes while Safwa runs
    Then the check runs once, however many polls that day sees
    And the time is read at every look, so one the owner moves counts from the next time it passes, without a restart
    And two hooks that name the same time share the one passing; two that name different times each get their own, and one look that sees both passed hands both on
    When Safwa starts after that time has already passed
    Then the check waits for the next day's time rather than running for the day it missed
    And a check that is switched off does not run, and switching it on after its time has passed does not run it for that day
    And a daily check's request not said by the local midnight after its time is dropped: the day it was about is over; one a turn is saying is that turn's to settle
    And work of its own that failed is tried again at every later look until it is done, and nothing else that time handed on is run again

  Scenario: AG-HOOK-042 — A hook may run before any Advisor turn, inside it
    Given a hook that does work of its own at the start of a turn
    When a turn begins — on the owner's word or from what Safwa owes them
    Then the hook runs inside that turn, after the conversation is read and before the model is
      asked, and a message it puts in the chat stands there before the answer
    And that message is not in the conversation of the turn it ran in, so the turn still ends on
      the owner's words
    When the owner's message takes the turn while the hook is still running
    Then the hook is stopped with the turn and puts nothing in the chat
    When the hook fails
    Then the turn runs on, and the failure is logged

  Scenario: AG-HOOK-043 — A hook may follow another hook's switch
    Given a hook that names another hook as its switch
    Then it has no switch of its own and is not on the Profile
    And it is on exactly when the hook it names is on
    When that hook is switched off
    Then this one's condition is not checked and its owed requests are dropped with it
    And a hook naming an unknown hook, or one without a switch of its own, is refused before the
      application starts

  Scenario: AG-RECEIPT-044 — A subagent may be shown as is, through the message Safwa writes
    Given a subagent declared as one whose words are shown as they are
    When it finishes with words
    Then those words are a block of the single message Safwa writes, printed before the rest of it,
      paragraphs and repeated lines intact, and Safwa is told they were shown and not to repeat
      them
    And the block is not a receipt: it is not handed to the next subagent of the request as work
      already saved
    And it is not made to open with a tool call: its words are the work
    When a screen stops the request between the subagent's answer and Safwa's
    Then the block is still there when the request carries on

  Scenario: AG-DONE-045 — Before Safwa answers the owner's message, the request is read for what was asked and not done
    Given the request review is on in the feature toggles
    When Safwa is about to answer the owner's message, by AG-HOOK-047
    Then one review reads it, and is told that a change the owner discarded, refused or took
      back is not missing, and neither is one the answer asks them about
    When it finds a change the message asked for that nothing made
    Then Safwa is told what is not done: to route it now, or to tell the owner it was not done
    When it finds nothing missing, or cannot reach a decision
    Then the answer is sent as it is
    When the request review is off in the feature toggles
    Then no answer is read before it is sent

  Scenario: AG-HOOK-046 — A check may send a subagent's whole response back before any of it is prepared
    Given a hook that checks the calls a subagent response carries
    When a subagent response carries calls that would become proposals
    Then the hook reads them with the text of that response, once, before any is prepared
    When it answers with words
    Then none of those calls is prepared, and each comes back refused with the hook's code and
      its words
    And when two such hooks would answer, the first in the registration order decides
    And each response sent back counts as one of the 5 tries (MAX_REPAIR_ROUNDS = 5)
    When the hook fails
    Then no call of that response is prepared, and Safwa is told that subagent did not finish

  Scenario: AG-HOOK-047 — A check may hold back Safwa's answer to the owner's message once
    Given a hook that checks the answer to a request
    When Safwa is about to answer the owner's message in words, after every screen of the request
    Then the hook reads the conversation, each change the request made with what became of it,
      and the answer
    When it answers with words
    Then the answer is not sent, those words are handed to Safwa, and the same request goes on
    And its next answer is sent without the hook reading it
    When Safwa answers a request of its own, not a message of the owner's
    Then the hook does not read it
    When the hook fails
    Then the answer is sent as it is

  Scenario: AG-HOOK-048 — A hook's request may carry a block that opens Safwa's message
    Given a hook whose request to the Advisor carries a block its feature wrote
    When that request is said
    Then the block opens the single message Safwa writes, paragraphs and repeated lines intact,
      and the Advisor is told it was shown and not to repeat it
    When requests of several hooks are said in one turn, and more than one carries a block
    Then each block is shown once, oldest request first, all of them before the Advisor's words
