Feature: What Safwa remembers across Sprints
  Safwa remembers what the retro analysis of each Sprint showed, and nothing else writes its memory:
  patterns that recur across Sprints, and the last analysed Sprint whole. The owner reads the same
  text the Advisor is given, and edits none of it — what they want Safwa told outright goes in the
  Profile.

  Numbers below name the constant they come from; the tests read the constant.

  Background:
    Given Sprints that ended, some of them analysed on the retro screen

  Scenario: MEM-RETRO-010 — Memory is what the retro left, and nothing else writes it
    Given a Sprint whose analysis memory has taken in
    When the Advisor is given its context, or the owner asks with /memory
    Then both read the same text: the patterns in three lists — what raised the day's rating, what
      lowered it, and what raised it in some Sprints and lowered it in others — each with how many
      Sprints showed it, and then the last analysed Sprint
    And /memory shows the whole of it, in as many messages as it takes, each within the limit
      (TELEGRAM_TEXT_LIMIT = 3900) and none replacing the one before it
    And nothing in the chat, no command and no file adds to it or takes from it
    And before any Sprint was analysed it says so, in one line

  Scenario: MEM-RETRO-011 — An analysed Sprint reaches memory on its own
    Given the analysis of a Sprint was written
    Then no button offers it to memory: a check every 60 seconds (MEMORY_RETRO_INTERVAL_SECONDS = 60)
      finds the oldest analysis memory has not taken in and takes it in, in the background
    And nothing is said in the chat about it
    And of the analyses owed, the Sprint that ended first goes first; a Sprint analysed after a
      newer one was taken in is taken in then, and a pattern is counted by the Sprints that
      observed it, whichever of them came in first

  Scenario: MEM-RETRO-012 — Taking a Sprint in waits its turn and is tried until it is done
    Given an analysis memory has not taken in
    When the Advisor is answering, or the owner writes while the model is being asked, or the
      call fails, or Safwa restarts before it is written
    Then nothing is written, the Sprint is still owed to memory, and the next check tries again
    And once its observations are written the Sprint is marked as taken in, in the same write, and
      no check takes it in a second time
    And analysing the Sprint again owes it to memory again

  Scenario: MEM-RETRO-013 — A pattern is what the Sprints observed of one thing
    Given the patterns memory holds, and a Sprint's analysis with what raised the day's rating
      and what lowered it
    When the Sprint is taken in
    Then each of its claims is an observation of the Sprint's: the claim as it worded it, and
      whether it raised the day's rating or lowered it
    And a claim that names what a pattern names, with either effect, is an observation of that
      pattern, which is worded as the earliest Sprint still observing it said it
    And a claim no pattern is about becomes a pattern with this one observation
    And a pattern is counted by the Sprints that observed it, those that saw it raise the day's
      rating and those that saw it lower it apart, a Sprint that saw both counting once; one with
      Sprints on both sides is shown with both counts, as no rule to plan by
    And what had nothing to do with the day's rating never becomes a pattern

  Scenario: MEM-RETRO-014 — A pattern nobody confirms is left out
    Given a pattern one Sprint observed
    When 3 Sprints taken in after it did not observe it (MEMORY_UNCONFIRMED_SPRINTS = 3)
    Then it is left out of what memory shows; a Sprint analysed but not yet taken in counts as
      none of the 3
    And memory shows at most 20 patterns (MEMORY_PATTERNS_MAX = 20); over that, the ones with the
      fewest Sprints go first, and of those the ones observed longest ago
    And the patterns are read strongest first, by Sprints and then by the latest of them
    And nothing is deleted for it: the observations stay, and a Sprint taken in later can bring a
      pattern back

  Scenario: MEM-RETRO-015 — The last analysed Sprint is read whole, and taking a Sprint in again gives the same memory
    Given several analysed Sprints
    Then memory carries the one that ended last: its number, the day it ended, whether its
      Success criteria were met as the analysis read it, its headline, the experiment it set —
      named as one whose result nothing checked — and what is worth knowing
    And none of its numbers, and nothing that had nothing to do with the day's rating, is carried
    When a Sprint already taken in is analysed again
    Then its own observations are replaced and no other Sprint's are touched: a claim it still
      makes keeps its pattern with no question to the model, a claim it makes anew is matched, and
      a claim it no longer makes is gone
    And so taking it in twice gives the same memory, and taking a corrected analysis in gives back
      what the earlier one had taken away
    And the last analysed Sprint is still the one that ended last

  Scenario: MEM-RETRO-016 — The model matches, and the code writes
    Given patterns in memory and a Sprint's claims
    Then the model is asked one thing, once: which of the Sprint's new claims names what which
      pattern names, in the same or other words and whatever the effect, numbered on both sides
    And it is not asked at all when memory holds no pattern or the Sprint made no new claim
    And a pair the model names with a number that is not there counts for nothing
    And an answer that cannot be read leaves memory as it was, and the Sprint owed
