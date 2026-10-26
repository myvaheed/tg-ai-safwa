Feature: The conversation in Telegram
  Safwa keeps the private chat as it happens: every message it sends, and every message of the
  owner's that is conversation, with the kind of message each one is. Before every answer it
  reads the conversation back out of what it kept, never out of Telegram.

  Background:
    Given a private chat between the owner and Safwa, kept message by message

  Scenario: TG-MARK-001 — A message Safwa sends is kept with its kind
    Given Safwa put a review screen in the chat
    When Safwa reads the conversation back
    Then the screen is not in it
    When Safwa rewrites that screen in place into an account of what became of it
    Then the account is in the conversation once, where the screen stood
    And it is still the same message, not a second one

  Scenario: TG-KIND-002 — Screens, receipts and progress notes are never part of the conversation
    Given a dashboard, a line reporting what a button just did, a progress note and an error
      message were all put in the chat
    When Safwa reads the conversation back
    Then none of the four is in it
    And what is in it is what was said: the owner's words, Safwa's answers, what Safwa said
      unasked, and the Summaries

  Scenario: TG-OWNER-003 — The owner's words are kept as they arrive
    Given the owner wrote to Safwa
    When Safwa reads the conversation back
    Then their message is in it as something they said
    And a value they typed into a field is not in it
    And text opening with "/" is never in it

  Scenario: TG-RELAY-004 — Words that never reached the chat as the owner's are put there as theirs
    Given the owner spoke instead of typing, so what they said is in the chat as a recording and
      not as words
    When Safwa has turned it into words
    Then it puts them in the chat as a message in the owner's name, before answering them
    And the conversation reads them back as the owner's words alone, without the name

  Scenario: TG-WINDOW-005 — How much conversation Safwa reads is a size, not a number of messages
    Given a conversation longer than Safwa reads at once
    When Safwa reads it back before answering
    Then it takes the newest messages until 6000 tokens are spent, counting everything an
      answer puts in front of the model — its calls and what came back for them included
      (SUMMARY_TRIGGER_TOKENS = 6000)
    And the cut falls between two messages, so no message arrives half there and no answer
      arrives without its calls
    And a hundred short messages may all fit where three long ones do not

  Scenario: TG-SUMMARY-006 — The window ends at the newest message that stands for what came before it
    Given the conversation has a message that stands for everything said before it
    When Safwa reads the conversation back before answering
    Then the window ends there, and that message is read in place of what it stands for
    And an older one of those is never read
    And up to 20 of the messages just before it come along with it, as their words alone
      (EDGE_CONTEXT_MESSAGE_LIMIT = 20)
    And which message that is, the window does not decide: in Safwa it is the newest Summary,
      by SUM-WRITE-001

  Scenario: TG-NOTES-007 — The conversation is what Safwa kept, not what the chat shows
    Given the owner deleted one of their messages from the chat
    And Safwa took one of its own messages out of the chat
    When Safwa reads the conversation back
    Then the owner's deleted message is still in it
    And the message Safwa took out is not

  Scenario: TG-CURRENT-008 — The message Safwa is answering is in the conversation exactly once
    Given the owner just sent a message and Safwa is answering it
    When Safwa reads the conversation back
    Then that message is in it once — never twice, and never missing

  Scenario: TG-RECEIPT-009 — What a change did comes back as the result of the call that made it
    Given Safwa's answer carried lines saying what an approved change did
    When Safwa reads that answer back a turn later
    Then the call that made the change comes first, then what it came back with, then the words
      Safwa said
    And the lines the owner read about the change are not part of Safwa's words

  Scenario: TG-SHAPE-010 — The conversation is laid out as turns, not as messages
    Given a stretch of conversation holding the owner's words, lines the interface wrote, and
      the answers Safwa gave
    When Safwa reads it back
    Then everything on the owner's side gathers into one turn of theirs, up to the next answer
    And an answer is Safwa's own calls and words, and nothing else
    And the time is written once for each hour of conversation, on the owner's side only

  Scenario: TG-EDIT-012 — An edit the owner makes is what the conversation reads
    Given the owner said something, and then edited that message
    When Safwa reads the conversation back
    Then it reads the edited words, where the message always stood
    And an edit to a value they typed into a field changes nothing

  Scenario: TG-CITE-011 — A link Safwa wrote reads back as the citation it wrote
    Given Safwa's answer pointed the owner at a Card, and they see a tappable link
    When Safwa reads that answer back a turn later
    Then it reads as the citation Safwa originally wrote, not as bare words
    And in words the interface wrote, a link to one of Safwa's items reads back as its citation
      too, and a link to anything else reads as the words it shows

  Scenario: TG-TOOLS-013 — A turn later, Safwa reads the calls its answer made
    Given Safwa read the workspace and handed the request to a subagent before answering
    When the owner's next message is answered
    Then that answer comes back as the calls it made, what came back for each, and then its words

  Scenario: TG-READS-014 — A read from an earlier turn comes back as its call, a receipt whole
    Given an earlier answer read rows of the workspace and saved a change
    When a later turn reads it back
    Then the read comes back as its call and a note to read again, without the rows it returned
    And what the change did comes back whole
    And no result comes back with the `next`, `hint` or `notice` it gave at that step

  Scenario: TG-CUE-015 — What Safwa said unasked reads back after what caused it
    Given Safwa spoke on its own, on a Reminder or on a request a check made
    When Safwa reads the conversation back
    Then that request comes first, on the owner's side, and Safwa's answer after it
    And two answers of Safwa's are never read as one

  Scenario: TG-SYSTEM-016 — A line the interface wrote is a system line, never Safwa's words
    Given the interface wrote into the chat itself — a review that closed or was interrupted,
      a Card created by hand, the onboarding notice, words no turn of Safwa's produced
    When Safwa reads the conversation back
    Then each is on the owner's side, between <system> and </system>, its lines all inside them
    And none of it reads as something Safwa said
    And the owner's words after it read as theirs, to the Advisor and to a subagent alike

  Scenario: TG-QUOTE-033 — A reply carries the start of the message it answers
    Given the owner replies in Telegram to a message in the chat, Safwa's or their own
    When Safwa reads the conversation back
    Then the reply's words follow <quote> and </quote> holding the first 100 characters of
      that message, cut with "…" (QUOTE_CHARS = 100)
    And when the owner quoted a part of that message, the part they quoted is held instead
    And a reply to a message with no words carries no quote
    And an edit the owner makes to the reply keeps its quote
    And the Advisor and a subagent reading the conversation are told what <quote> holds

  Scenario: TG-THINK-017 — Safwa's reasoning goes back between its calls and is never kept
    Given the model returned its reasoning beside a call it made
    When the same session asks the model again, after that call's result
    Then the call comes back with that reasoning, unchanged
    And the answer kept in the chat carries none of it

  Scenario: TG-IMAGE-018 — A photo the owner sends is kept as their message, under a short label
    Given the owner sent a photo with a caption
    When Safwa reads the conversation back
    Then the photo is in it as something the owner said: its label, then the caption
    And the label cites that photo by its number, under a description written when it arrived
    And the description is asked for in at most 5 words (DESCRIPTION_MAX_WORDS = 5), from the
      photo, its caption, the owner's name and the last 3 exchanges of the conversation
      (DESCRIPTION_EXCHANGES = 3)
    And the label reads the same in every later turn
    And a photo sent without a caption is in it as its label alone

  Scenario: TG-ALBUM-019 — Photos sent together as an album are one message, answered once
    Given the owner sent several photos at once, with a caption on one of them
    When Safwa answers
    Then it answers once, for every photo and the caption together
    And each photo has a label of its own
    And none of the photos is taken out of the chat

  Scenario: TG-SIGHT-020 — The conversation carries a photo's label, never the photo
    Given the conversation holds a photo
    When Safwa, a subagent, the Summary or a day read back reads the conversation
    Then each reads the photo's label and caption, never the photo itself
    And the photo itself is seen only when its label is written, and by a tool made to read
      photos, which answers in words

  Scenario: TG-OFF-021 — A photo sent where images are off is refused plainly
    Given the application runs without image input
    When the owner sends a photo
    Then Safwa says image input is off
    And nothing is answered, and the photo is not in the conversation

  Scenario: TG-RELOOK-022 — Safwa looks at a photo again only where it takes photos
    Given the application takes photos
    When the owner asks what a photo sent earlier shows
    Then Safwa looks at that photo again and answers in words, with no subagent
    And the photo itself does not enter the conversation
    But where images are off, Safwa has no way to look at a photo

  Scenario: TG-USAGE-024 — Usage counts elapsed activity once with a two-minute quiet window
    Given the application records its owner's usage
    When the owner sends a message, command or presses a button
    Then activity lasts for two minutes after that action
    And another action extends that quiet window
    And overlapping intervals count once, with future time never counted in advance
    And a longer quiet gap adds no time, including across midnight

  Scenario: TG-VOICE-025 — An own recording adds its duration before it was sent
    Given the owner sends a new voice message or video note
    Then its duration is included as an approximate interval before the message was sent
    And that interval counts only once where it overlaps other activity
    But a forwarded recording or uploaded audio file adds no recording duration

  Scenario: TG-WAIT-026 — Usage includes the owner's full request but no autonomous work
    Given the owner requested an answer
    Then usage includes waiting for transcription, tools, subagents and delivery of the answer
    And a delivered answer starts another two-minute window for reading
    But work after the answer and autonomous Cues or hooks add no usage
    And cancellation or an unhandled failure ends the wait, and its two-minute window follows

  Scenario: TG-PAUSE-027 — A review pauses the wait until the owner decides
    Given the owner's request stopped on Save or Discard
    Then its reading window lasts for two minutes after that screen
    And time after that window is not counted while the owner has not decided
    When the owner decides and the request continues
    Then its new work is counted, followed by the reading window of its answer

  Scenario: TG-RESTART-028 — Restart keeps accumulated usage without counting downtime
    Given the owner has accumulated usage and an owner's request is running
    When the application stops before that request ends and starts again
    Then the accumulated usage remains
    And the interrupted wait adds nothing beyond the two-minute window of the owner's action
    And time until restart is not added

  Scenario: TG-KEEP-029 — Usage survives clearing the conversation
    Given the owner has accumulated usage
    When the chat is cleared or its kept messages are forgotten
    Then the accumulated usage remains

  Scenario: TG-EVENT-030 — Usage belongs to owner actions, and an edit repeats no recording
    When the owner edits a message
    Then the edit is a new action, without repeating any recording duration
    But events from another user or a group add no usage

  Scenario: TG-LOCK-031 — Protected access gates every update
    Given the application enables secret-word access and the chat is locked
    Then messages, commands, callbacks, deep links, edits and media cannot reach their handlers
    And an attempted input asks for Secret word without changing the dialogue or cancelling a hook
    And only an exact new text message containing the word unlocks access
    And a restart requires the word again and retains pending hook requests
    And a clear Telegram refuses leaves access locked and is tried again until the messages are gone
    And Reminder words are delivered, separately from pending hook requests
    And hooks are not prepared or generated while access is locked
    And a Reminder proposal made while locked is resolved as Discard, and its continuation reaches the chat
    When the owner unlocks access
    Then running background work finishes first, attempts and prompts are cleared, and what was said unasked since the owner last wrote stays
    But the last text, command, recording or photo attempted before the word that is not a try at the word stays, and once access opens it is processed exactly once as if sent then
    And a try at the word is an ordinary text of at most 5 characters (SECRET_LIKE_LENGTH = 5) or at most 3 character edits from it (SECRET_LIKE_DISTANCE = 3)
    And commands are never treated as tries at the word, even when short or similar to it
    And pending hooks run at unlock when no such attempt stays, and otherwise once the chat is free after processing it
    And Home is shown only if the application supplies an unlock renderer
    And commands and item links received after verification wait until access opens and then reach their handlers
    And commands and item links received before verification remain attempted inputs
    And pending hooks may open their normal screens and reviews after verification
    And the Advisor reads the hooks and Reminders left in the chat
    And a failed unlock leaves access closed and pending hooks waiting
    But applications without protection keep their ordinary behavior

  Scenario: TG-CLEAR-032 — A clear takes out everything but what was said unasked and not yet answered, and the conversation starts with what it left
    Given the application empties the chat, after a quiet time or when the owner asks
    Then every message is taken out, from both sides: screens, receipts, the owner's words and
      the answers to them
    And no screen is left, so nothing is left waiting for a typed value
    But a message older than 48 hours stays in the chat (TELEGRAM_DELETE_WINDOW = 48 hours)
    When the clear comes after a quiet time
    Then what Safwa said unasked after the owner last wrote to the Advisor stays, through repeated
      clears, until the owner writes to the Advisor again
    When the owner asked for the clear
    Then what Safwa said unasked is taken out too
    And what was said stays kept, and a day read back still reads all of it
    And a screen, a progress line or anything else that was not said is forgotten with its message
    When Safwa reads the conversation back
    Then it begins with the first message the newest clear left in the chat, or after the clear when it left none
    And nothing said before that is read
    And an application that never clears has nothing taken out and nothing started over
    When the owner asks for the clear while a conversation with an agent is open
    Then that conversation is ended first, by TG-SUB-036

  Scenario: TG-SUB-034 — A conversation with an agent is a stretch of the chat that the conversation never reads
    Given a conversation with an agent opens after the chat's newest message
    Then every message after it, from both sides, is that conversation's until it ends, and
      nothing is written on a message to say so
    And no reader of the conversation reads a message of it: the window and a day read back alike,
      however long it is
    And an edited message stays in the conversation it was said in
    And the result stands in the conversation in its place, newer than all of it
    When it ends
    Then its messages are taken out of the chat
    But a message Telegram refuses to delete stays in the chat, and out of the conversation

  Scenario: TG-SUB-035 — One conversation with an agent at a time, and the first ending decides
    Given a conversation with an agent is open in the chat
    Then no second one opens in that chat
    And when two endings come at once, the first decides and the other changes nothing
    And once it has ended, the chat is free for the next one

  Scenario: TG-SUB-036 — /exit ends the conversation with an agent without a result
    Given a conversation with an agent is open
    When the owner sends /exit
    Then it ends, the agent is not asked, and the conversation reads "The conversation was ended
      without a result." in its place (EXIT_RESULT)
    And /exit with none open says "No conversation with an agent is open." (NOTHING_TO_EXIT)
    And only an application with an agent that has a title offers /exit
    When the owner asks for the chat to be cleared
    Then it ends as /exit ends it, before the clear, and is not read again
    When its agent is no longer declared
    Then the owner's next message ends it as /exit ends it, and Safwa answers that message

  Scenario: TG-SUB-037 — A result that never reached the chat is delivered once, before anything is read
    Given a conversation with an agent ended, and the process stopped before its result was sent
    When the owner writes next
    Then the result is sent first, once, and Safwa reads it with the owner's words
    And no message of the conversation that ended is read

  Scenario: TG-SUB-038 — Locking the chat leaves an open conversation with an agent to go on
    Given a conversation with an agent is open
    When the chat is locked and opened again with the secret word
    Then the conversation is still open, and the owner's next message goes on with it
    And its agent still reads every message of it, though the clears that came with the lock took them out of the chat
