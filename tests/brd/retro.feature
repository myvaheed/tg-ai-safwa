Feature: Retro
  The retrospective is the screen a finished Sprint leaves behind: what it added up to, written
  down as it ended, and what Safwa makes of those numbers when the owner asks it to.

  Background:
    Given a Sprint that has ended, and the message Safwa sent about it (PL-END-015)

  Scenario: RT-OPEN-001 — A Sprint that ended keeps a screen of its own
    Given the owner taps the "Sprint retro" link at the end of that message
    Then a screen names that Sprint by its number, the dates it ran, and the Success criteria
      it was given
    And below them, what the Sprint added up to (RT-STATS-003)
    And it arrives as its own message, offering the owner's mark on the criteria (RT-CRIT-004),
      the analysis (RT-AI-005) and the way back to the menu
    And a Sprint still running has no retro: asked for it, Safwa says it is written when the Sprint ends

  Scenario: RT-OPEN-002 — The retro opens the way every other cited item does
    Given Safwa is answering a question about a Sprint that has ended
    Then it may name the retro as a link the owner can tap
    And asked to see that retro, it puts the screen up itself, exactly as it does for a Card,
      a Check, a Value, a Tag, a Request and a day of the Diary

  Scenario: RT-STATS-003 — The retro adds the Sprint up from its own record
    Given the Sprint's commitments, the Actions they name, and the Checks tied to a Value
    Then the screen shows the effort taken into the Sprint — the initial plan and what was added along the way together — and the effort finished, with the finished share in whole percent
    And of the effort taken: the initial plan, what was added, and what was taken back out
    And how many Actions finished, how many remain in the Sprint, and how many of those are blocked; an Action taken out is in none of the three
    And for each Check series tied to a Value, answered while the Sprint ran: Passed and Missed counts, by the Check's title and its Values
    And a Check tied to no Value is not on the screen, and neither is a series with no answer in the Sprint
    And every number is written down as the Sprint ends, off the record and by no model, and never added up again: an Action unblocked or deleted afterwards, or a Check re-linked, changes nothing on the screen
    And the record also keeps, for the analysis to read: how many Actions were taken in, how many
      of the Actions the Success criterion rests on there were, how many of them finished and how
      many Actions the model had not yet told key or not, the effort and the Actions taken in and
      finished by Category and by Energy type — an Action with two of either counts in both, and one
      with none counts as none — and one row per local day the Sprint ran: the Actions in Today that
      morning, the Actions finished that day, and how those fell by Category and Energy type
    And the days run from the day the Sprint started to the day it ended or its planned end,
      whichever came first, and are there even when every Action of the Sprint was deleted
    And the effort on the Sprint screen while it runs and the effort in its record are added up by
      the same code

  Scenario: RT-CRIT-004 — Whether the Success criteria were met is the owner's word
    Given the retro screen of a Sprint that ended
    Then under the Success criteria it says whether they were met: yes, no, or not marked yet
    And the owner marks it on that screen with one tap, and may change the mark to either of the
      other two
    And no model marks it, and a Sprint still running has no mark to set

  Scenario: RT-AI-005 — Analysing a Sprint is one run, watched on one message
    Given the retro screen of a Sprint that ended
    When the owner taps "Analyse with AI"
    Then the retro screen loses its buttons while the run goes, so there is nothing on it to tap
    And below it one progress message shows the run from 0 to 100 percent, moved on as each model
      answer arrives, and it is taken out of the chat when the run ends, however it ends
    And the run holds the turn the way work Safwa does unasked holds it: a message or a tap from
      the owner ends it, and nothing of a run that did not reach its last call is kept
    And the analysis is written and its screen drawn while the run still holds the turn; a run
      whose turn was taken writes nothing
    And a run that fails says so in one message, and the retro screen comes back with its buttons

  Scenario: RT-AI-006 — The analysis reads the Sprint's record and the Diary, nothing else
    Given a Sprint that ended, and the Sprints that ended before it
    Then the numbers come from each Sprint's record as written when it ended (RT-STATS-003), for
      this Sprint and the 2 that ended before it (RETRO_SPRINTS_BEFORE = 2), or fewer when fewer ended
    And each Sprint comes with its Success criteria and the owner's mark (RT-CRIT-004), read as "?"
      for a Sprint not marked, and with how many Actions the model had not yet told key or not, so
      a Sprint never classified is not read as one with no key Action
    And the Diary of this Sprint's days is read as it reads today, whole, never cut
    And the model is given no tool that reads the database

  Scenario: RT-AI-007 — The run is small questions, each answered with one call
    Given the run starts
    Then three overview questions are asked at once — the Sprints' totals and criteria, the shares
      by Category, the shares by Energy type — each answered with one call whose first field is
      verbose_analyse, the model's own thinking
    And the Sprint's days are read in batches of 3 (ANALYSIS_DAY_BATCH = 3), asked at once with the
      overview, each batch answered with what likely raised the day's rating, what likely lowered
      it, and what had nothing to do with it
    And each of those three lists is then reviewed alone, asked at once: a claim said twice, backed
      by another, or resting on two or more days is confirmed, a claim whose opposite is in the same
      list is contradicted, and a claim resting on one day alone is left aside; an empty list is not
      asked about
    And the three confirmed lists are then read together in one call, numbered: a claim standing in
      two of them, in the same or other words, is named by its numbers and taken out of both, and
      the record keeps what was taken out; fewer than two lists with a claim are not asked about
    And one last call writes the analysis from the overview findings and the confirmed claims left
    And every question is asked in English and answered in the language the owner writes in

  Scenario: RT-AI-008 — What the run leaves behind
    Given the run reached its last call
    Then the Sprint keeps the analysis in place of one an earlier run left
    And the owner reads it as one screen, headed by the days the run read rather than the planned
      dates: the headline, each trend, what raised the day's rating, what lowered it, what had
      nothing to do with it, and one experiment for the next Sprint
    And the screen says when the run was and the owner's mark as the run read it; a mark changed
      since is named on the screen and counts only once the Sprint is analysed again
    And the screen is one Telegram message: the last call refuses more than 6 trends
      (TRENDS_MAX = 6), more than 3 items in a list (ITEMS_MAX = 3), a headline or an experiment
      over 200 characters (SENTENCE_CHARS = 200), an item over 140 (ITEM_CHARS = 140), a trend's
      metric over 40 or its note over 120 (METRIC_CHARS = 40, NOTE_CHARS = 120), and a fact over
      120 (FACT_CHARS = 120); the model's thinking aloud has no such limit
    And the fact it drew about the owner, if any, is offered on that screen as one button, and the
      button writes it to memory.md the way a fact typed with /mem is written (MEM-FILE-009), headed
      by the Sprint it was drawn from
    And once written it is not offered again, and no later run takes it back out of memory.md
