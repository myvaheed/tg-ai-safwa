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

  Scenario: TG-EDIT-012 — An edit the owner makes is what the conversation reads
    Given the owner said something, and then edited that message
    When Safwa reads the conversation back
    Then it reads the edited words, where the message always stood
    And an edit to a value they typed into a field changes nothing

  Scenario: TG-CITE-011 — A link Safwa wrote reads back as the citation it wrote
    Given Safwa's answer pointed the owner at a Card, and they see a tappable link
    When Safwa reads that answer back a turn later
    Then the link reads as the citation Safwa originally wrote, not as bare words
    And a link to anything that is not one of Safwa's own items is left exactly as it is
