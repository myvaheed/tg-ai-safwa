Feature: The conversation in Telegram
  Safwa keeps no copy of what was said. Before every answer it reads the real private chat back,
  and what it finds there is the conversation. So a message that is still in the chat counts, one
  that was taken out of it does not, and a message Safwa sent says for itself whether it was
  something said or a screen to act on.

  Background:
    Given a private chat between the owner and Safwa, read back message by message

  Scenario: TG-MARK-001 — A message Safwa sent off the record is not part of the conversation
    Given a message Safwa put in the chat without saying what kind of message it was
    When Safwa reads the conversation back
    Then that message is not in it
    And on the messages that do say, the marking is invisible to the owner
    And a message Safwa rewrites in place stays the same message, so rewriting a review screen
      into an account of what became of it does not add a second one

  Scenario: TG-KIND-002 — Screens, receipts and progress notes are never part of the conversation
    Given a dashboard, a line reporting what a button just did, a progress note and an error
      message were all put in the chat
    When Safwa reads the conversation back
    Then none of the four is in it
    And what is in it is what was said: the owner's words, Safwa's answers, what Safwa said
      unasked, and the Summaries

  Scenario: TG-OWNER-003 — Owner text still in the chat is what the owner said
    Given a message the owner sent themselves, carrying no marking of any kind
    When Safwa reads the conversation back
    Then it counts as something they said
    And a command, or a value they typed into a field, is not found at all — Safwa took each of
      those out of the chat as it read them
    When a command Safwa failed to take out is left standing in the chat
    Then it is still not part of the conversation, because text opening with "/" never is

  Scenario: TG-RELAY-004 — Words that never reached the chat as the owner's are put there as theirs
    Given the owner spoke instead of typing, so what they said is in the chat as a recording and
      not as words
    When Safwa has turned it into words
    Then it puts them in the chat as a message in the owner's name, before answering them
    And the conversation reads them back as something the owner said

  Scenario: TG-WINDOW-005 — How much conversation Safwa reads is a size, not a number of messages
    Given a conversation longer than Safwa reads at once
    When Safwa reads it back before answering
    Then it takes the newest messages until 6000 tokens are spent
      (SUMMARY_TRIGGER_TOKENS = 6000)
    And the cut falls between two messages, so no message arrives half there
    And a hundred short messages may all fit where three long ones do not

  Scenario: TG-SUMMARY-006 — The window ends at the newest message that stands for what came before it
    Given the conversation has a message that stands for everything said before it
    When Safwa reads the conversation back before answering
    Then the window ends there, and that message is read in place of what it stands for
    And an older one of those is never read
    And up to 20 of the messages just before it come along with it
      (SUMMARY_CONTEXT_MESSAGE_LIMIT = 20)
    And which message that is, the window does not decide: in Safwa it is the newest Summary,
      by SUM-WRITE-001

  Scenario: TG-NOTES-007 — The conversation is read from the chat, and from nothing Safwa keeps
    Given every note Safwa kept about this chat is gone
    When Safwa reads the conversation back
    Then the marking on its own messages still says what each of them was
    And the owner's surviving messages still count as theirs
    And the conversation reads as it did
    And what stops the read is the token budget, or the newest Summary — never a note

  Scenario: TG-CURRENT-008 — The message Safwa is answering is in the conversation exactly once
    Given the owner just sent a message and Safwa is answering it
    When Safwa reads the conversation back
    Then that message is in it once — never twice, and never missing

  Scenario: TG-RECEIPT-009 — A line reporting what was saved is not something Safwa said
    Given Safwa's answer carried lines saying what an approved change did
    When Safwa reads that answer back a turn later
    Then those lines come back as the outcome of the work, placed with the request they answered
    And what is left of the message is what Safwa actually said to the owner

  Scenario: TG-SHAPE-010 — The conversation is laid out as turns, not as messages
    Given a stretch of conversation holding the owner's words, the outcomes of work Safwa did, and
      the answers Safwa gave
    When Safwa reads it back
    Then everything that is not an answer of Safwa's gathers into one turn of the owner's, up to
      the next answer
    And answers with nothing between them are read as one answer
    And the time is written once for each hour of conversation, not once per message

  Scenario: TG-CITE-011 — A link Safwa wrote reads back as the citation it wrote
    Given Safwa's answer pointed the owner at a Card, and they see a tappable link
    When Safwa reads that answer back a turn later
    Then the link reads as the citation Safwa originally wrote, not as bare words
    And a link to anything that is not one of Safwa's own items is left exactly as it is
