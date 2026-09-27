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

  Scenario: TG-CUE-015 — What Safwa said unasked reads back after what caused it
    Given Safwa spoke on its own, on a Reminder or on a request a check made
    When Safwa reads the conversation back
    Then that request comes first, on the owner's side, and Safwa's answer after it
    And two answers of Safwa's are never read as one

  Scenario: TG-SYSTEM-016 — A line the interface wrote is a system line, never Safwa's words
    Given the interface wrote into the chat itself — a review that closed or was interrupted,
      a Card created by hand, the onboarding notice, words no turn of Safwa's produced
    When Safwa reads the conversation back
    Then each is on the owner's side, marked as a system line
    And none of it reads as something Safwa said

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

  Scenario: TG-HOME-023 — A Home message clears the chat down to itself, and the conversation starts after it
    Given the application puts a Home message in the chat, from a check on a schedule (AG-HOOK-050)
    Then it arrives as a new message that makes no sound
    And every message above it, back to the previous Home message, is taken out of the chat, the owner's and Safwa's alike
    But a message older than 48 hours stays in the chat (TELEGRAM_DELETE_WINDOW = 48 hours)
    And what was said stays kept, and a day read back still reads all of it
    And a screen, a progress line or anything else that was not said is forgotten with its message
    When Safwa reads the conversation back
    Then it begins after the newest Home message: nothing said before it, a Summary included, and not the Home message itself
    And an application that never puts a Home message in the chat has nothing taken out and nothing started over
