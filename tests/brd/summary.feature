Feature: The running Summary of the conversation
  Telegram is where the conversation really lives, and a long conversation outgrows what Safwa can
  read at once. A Summary is the message that stands for everything said before it, so the older
  part still counts.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given a conversation in Telegram

  Scenario: SUM-WRITE-001 — A Summary is written when the window fills, and not before
    Given the conversation is under 6000 tokens (SUMMARY_TRIGGER_TOKENS = 6000)
    Then the check that runs after every turn writes no Summary
    But a Summary asked for outright may still be written

  Scenario: SUM-WRITE-002 — A new Summary is written from the old one and what happened since
    Given an earlier Summary, and conversation that happened after it
    When another Summary is written
    Then it is written from both, so nothing the earlier one covered is lost

  Scenario: SUM-AUTO-003 — Disabling automatic Summary keeps the explicit command
    Given the automatic Summary reaction is disabled in the connection list
    When the owner finishes a turn
    Then no automatic Summary is attempted
    When the owner asks for /summarize
    Then the ordinary Summary writer still handles that request
