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
      the analysis (RT-AI-005), its charts (RT-CHART-018) and the way back to the menu
    And a Sprint still running has no retro: asked for it, Safwa says it is written when the Sprint ends

  Scenario: RT-OPEN-002 — The retro opens the way every other cited item does
    Given Safwa is answering a question about a Sprint that has ended
    Then it may name the retro as a link the owner can tap
    And asked to see that retro, it puts the screen up itself, exactly as it does for a Card,
      a Check, a Value, a Tag, a Request and a day of the Diary

  Scenario: RT-STATS-003 — The retro adds the Sprint up from its own record
    Given the Sprint's commitments, the Actions they name, and the Checks tied to a Value
    Then with Effort Points on, the screen shows the effort taken into the Sprint — the initial plan and what was added along the way together — and the effort finished, with the finished share in whole percent when every Action was estimated
    And of the effort taken: the initial plan, what was added, and what was taken back out
    And how many Actions finished, how many remain in the Sprint, and how many of those are blocked; an Action taken out is in none of the three
    And for each Check series tied to a Value, answered while the Sprint ran: Passed and Missed counts, by the Check's title and its Values
    And a Check tied to no Value is not on the screen, and neither is a series with no answer in the Sprint
    And every number is written down as the Sprint ends, off the record and by no model, and never added up again: an Action unblocked or deleted afterwards, or a Check re-linked, changes nothing on the screen
    And the record also keeps, for the analysis to read: how many Actions were taken in, how many
      of the Actions the Success criterion rests on there were, how many of them finished and how
      many Actions the model had not yet told key or not, the effort and the Actions taken in and
      finished by Category, by Energy type and by each Category and Energy type together — an
      Action with two of either counts in both, and one with none counts as none — and one row per
      local day the Sprint ran: the Actions in Today that
      morning, the Actions finished that day, and how those fell by Category and Energy type
    And the days run from the day the Sprint started to the day it ended or its planned end,
      whichever came first, and are there even when every Action of the Sprint was deleted
    And the effort on the Sprint screen while it runs and the effort in its record are added up by
      the same code
    And planned counts and EP include reserved repetitions, including Category, Energy, key, blocked and morning quantities; actual Done and time count once per finished copy (PL-REPEAT-032, PL-REPEAT-033)
    And unknown schedule quantities mark planned totals as lower bounds and suppress exact planned shares and completion percentages (PL-REPEAT-034)

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
      it, what had nothing to do with it, and what happened those days that the next Sprint should
      know about
    And a claim rests on its batch's own days, each once: a day the model named twice, or one
      outside the batch, is not a second day, and a claim with none of the batch's days left rests
      on none
    And each of those three lists is then reviewed alone, asked at once, every claim with the
      batch that made it: a claim said in two or more batches, backed by another, or resting on two
      or more days is confirmed, a claim whose opposite is in the same list is contradicted, and a
      claim one batch made about one day, backed by nothing, is left aside; an empty list is not
      asked about
    And the three confirmed lists are then read together in one call, numbered: a claim standing in
      two of them, in the same or other words, is named by its numbers and taken out of both, and
      the record keeps what was taken out; fewer than two lists with a claim are not asked about
    And one last call writes the analysis from the overview findings, the confirmed claims left and
      every event the batches named; an event is not a claim, so nothing reviews it
    And every question is asked in English and answered in the language the owner writes in

  Scenario: RT-AI-008 — What the run leaves behind
    Given the run reached its last call
    Then the Sprint keeps the analysis in place of one an earlier run left
    And the owner reads it as one screen, headed by the days the run read rather than the planned
      dates: the headline, each trend, what raised the day's rating, what lowered it, what had
      nothing to do with it, one experiment for the next Sprint, and what is worth knowing next
      Sprint
    And the screen says when the run was and the owner's mark as the run read it; a mark changed
      since is named on the screen and counts only once the Sprint is analysed again
    And the screen is one Telegram message: the last call refuses more than 6 trends
      (TRENDS_MAX = 6), more than 3 items in a list (ITEMS_MAX = 3), a headline or an experiment
      over 200 characters (SENTENCE_CHARS = 200), an item over 140 (ITEM_CHARS = 140), a trend's
      metric over 40 or its note over 120 (METRIC_CHARS = 40, NOTE_CHARS = 120); the model's
      thinking aloud has no such limit
    And nothing on the screen offers the analysis to memory: from the moment it is written the
      Sprint is owed to memory, and memory takes it in on its own (MEM-RETRO-011)

  Scenario: RT-TIME-009 — A Sprint that ends with Time tracking on keeps its time
    Given a Sprint that ends while Time tracking is on in the Profile, some of its finished Actions carrying a time
    Then its record keeps that Time tracking was on, and how long the active day was, from the Morning time to the Diary time
    And the minutes of the finished Actions that carry a time, how many of them carry one and their effort; the minutes, the Actions with a time and their effort by Category and by Energy type; and the 3 longest by title and minutes (LONGEST_SHOWN = 3)
    And an Action with two Categories counts in both, and one with none counts as none, as effort does
    And a time recorded after the Sprint ended changes nothing in its record
    And a Sprint that ends while Time tracking is off keeps no time, and neither does one that ended before its record could

  Scenario: RT-TIME-010 — The retro shows how the Sprint's time went
    Given a Sprint that ended while Time tracking was on
    Then its retro screen has a Time section after the Actions, whether Time tracking is on or off now
    And it shows the time in all, a day on average over the Sprint's days, and that day as a share of the active day
    And with Effort Points on and all Actions estimated, the effort points an hour, and always how many of the finished Actions carry a time out of all of them
    And for each Category and each Energy type with a time: the time, its share of the time and the time per Action
    And with Effort Points on and all Actions estimated, those rows also show effort points an hour
    And the 3 longest Actions by title and time
    And every average and every rate is over the Actions that carry a time
    And a Sprint that ended while Time tracking was off has no Time section

  Scenario: RT-TIME-011 — The analysis reads how much of the active day was tracked, and nothing else of time
    Given the Sprints the analysis compares (RT-AI-006)
    Then the question about their totals and criteria gives each Sprint that ended with Time tracking on its share of the active day tracked, on average, and each other one as not tracked
    And the model is asked to set that share beside how each Sprint went
    And with no compared Sprint tracked, the question says nothing of time
    And no other number of time reaches the analysis

  Scenario: RT-LIST-012 — Every Sprint that ended is one tap from the menu
    Given three Sprints have ended and one is running
    When the owner opens Retro from the menu or with /retro
    Then the three are listed newest first, ten to a page (RETRO_LIST_PAGE_SIZE = 10)
    And each shows its number, its dates, its Success criteria mark and whether it was analysed
    And the running Sprint is not listed
    And below them, the charts of the newest Sprints that ended (RT-CHART-018)
    And the way into Life in weeks (LF-OPEN-001), there whether or not a Sprint has ended
    When the owner taps one
    Then its retro screen replaces the list, with a way back to the same page
    When no Sprint has ended yet
    Then Retro says so, and that a retro is written when a Sprint ends

  Scenario: RT-ASK-013 — A question over several ended Sprints is answered from their records
    Given three Sprints have ended
    When the owner asks for an average over the last three, such as the capacity they started
      with or the effort they finished
    Then Safwa answers with the average the code worked out over the three records
    When the owner asks for a total over them, such as the Actions they finished
    Then Safwa answers with the sum the code worked out, and no number is added up by the model
    And a Sprint with no capacity, no tracked time or no mark is left out of that one number,
      and the answer says over how many Sprints it is
    And each Sprint is named by its number with a link to its retro
    And a running Sprint is never in the answer, since it has no record yet (RT-OPEN-001)

  Scenario: RT-ASK-014 — A date finds the Sprint whose days it falls in
    Given a Sprint ran from 01.09 to 14.09 and ended on 12.09
    When the owner asks about the Sprint of 10.09
    Then Safwa finds that Sprint
    When the owner asks about 13.09
    Then Safwa says no Sprint was running that day

  Scenario: RT-OPEN-015 — Asked to show a retro, Safwa puts it on screen
    Given the owner names an ended Sprint by its number or by a date in it
    When they ask to see its retro
    Then its retro screen arrives after Safwa's words, as it does from the link (RT-OPEN-002)

  Scenario: RT-EP-016 — A retro distinguishes missing estimates from zero effort
    Given a Sprint ended with both estimated and unestimated Actions
    Then its record keeps the estimates it had when each Action joined and counts the missing ones
    And later estimates change neither that record nor its counts
    When Effort Points are off
    Then the retro screen, records read in words and AI analysis use Action counts and hide EP and capacity
    When Effort Points are on
    Then the screen names partial totals and missing estimates, with no effort percentage or EP per hour
    And incomplete Sprints are left out of effort averages, with the number of included Sprints stated
    And time and Action counts are available in either mode

  Scenario: RT-ASK-017 — Sprints are chosen by number, by two dates, or all of them
    Given Sprints have ended and one is running
    When Safwa reads their records or adds them up
    Then it chooses them by their numbers, by a first and a last date, or by neither
    And two dates choose every Sprint with a day between them, whole, as one date finds the
      Sprint it fell in (RT-ASK-014)
    And neither chooses every Sprint that ended
    And the running Sprint is never chosen, since it has no record yet (RT-OPEN-001)
    And dates no Sprint ran in are not a mistake: the answer says no Sprint that ended has a day
      between them, and when the Sprints that ended ran
    When the choice is numbers and dates together, one date without the other, a date that is
      not one, or a first date after the last
    Then nothing is read, and the refusal names the call to make instead, built from what was
      sent: the numbers or the dates alone, today as the missing last date, the first day of the
      first Sprint that ended as the missing first, the date written as YYYY-MM-DD, or the two
      dates swapped
    When more Sprints are chosen than one read of whole records holds (RETRO_DATA_MAX = 6)
    Then the refusal lists their numbers and points to the sum and the mean, which take any
      number of Sprints

  Scenario: RT-CHART-018 — The charts of the Sprints that ended arrive as one album
    Given Sprints have ended
    When the owner taps "📈 Charts" on a Sprint's retro, or "📈 Charts of recent Sprints" on the
      Retro list
    Then the charts of that Sprint, or of the newest 12 Sprints that ended
      (CHART_SPRINTS_READABLE = 12), arrive as one album in place of the screen they were asked
      from
    And while they are drawn, the chat shows a photo being sent
    And below it one message names the Sprints and the days they cover, says how many of how
      many ended when there are more, and leads back to that retro or that page of the list, and
      to the menu
    And each chart names the Sprints it covers and the days from the first one's start to the last
      one's end
    And every number on them is read off the Sprints' records (RT-STATS-003) by code; no model
      draws or counts anything

  Scenario: RT-CHART-019 — The album holds the charts its Sprints carry
    Given the Sprints chosen for the charts
    Then the album always holds the Actions finished day by day against the Actions each Sprint
      took, what was taken and finished by Category and by Energy type, and the Actions finished
      a day on each weekday
    And once a record keeps Category and Energy type together and anything finished, how what
      finished in each Category went to each Energy type
    And with two Sprints or more, the Actions each one took and finished with the owner's mark on
      its Success criteria, and the Category mix of what each one finished
    And with two Sprints or more and Effort Points on, the initial plan, what was added, what
      finished and the capacity of each Sprint estimated in full that knew its Schedule
      quantities; the chart says how many were left out
    And with a Sprint that ended with Time tracking on, the time tracked in all, a day and as a
      share of the active day, by Category, and the 3 longest Actions, saying over how many
      Sprints it is
    And with a Check tied to a Value answered while they ran, its Passed and Missed, the most
      answered first and at most 8 by name (CHECKS_SHOWN = 8)
    And what was taken and finished counts Effort Points when they are on and every chosen Sprint
      was estimated in full and knew its Schedule quantities, and Actions otherwise
    And when a chosen Sprint's Schedule quantities are unknown, what was taken is written as at
      least so much, with no share finished, and the chart says its planned totals are lower
      bounds (RT-STATS-003)
    And each Sprint's own numbers are written on the charts only up to 12 Sprints
      (CHART_SPRINTS_READABLE = 12), and there they do not overlap
    And a Check is named by its title over its Values, clear of its bars
    And text the owner wrote, a Check's title, a Value or an Action's title, is drawn without
      the characters the font cannot draw, such as emoji
    And the album never holds more than Telegram allows (TELEGRAM_ALBUM_LIMIT = 10)

  Scenario: RT-CHART-020 — A Category and an Energy type look the same on every chart
    Given a chart that shows Categories or Energy types
    Then each one carries its emoji as Cards show it, and a colour of its own, the same on every
      chart
    And no two of them share a colour, and an Action with none of either is grey

  Scenario: RT-CHART-021 — Charts asked for in words go to the chat before Safwa's line about them
    Given Sprints have ended
    When the owner asks Safwa for charts of some of them, or for one chart by its name
    Then the Sprints are chosen as RT-ASK-017 chooses them, a choice made wrong is refused the
      same way, and any number of them is drawn
    And the charts of RT-CHART-019, or only the one asked for, go to the chat as they are drawn,
      with no message of words or buttons of their own, the chat showing a photo being sent
      meanwhile
    And Safwa's answer follows them, in one short line
    And they stay in the chat as a message: the next screen does not take them out
    When a number names no Sprint that ended
    Then nothing is sent, and the refusal says why and asks for the numbers of Sprints that ended
    When the chart asked for is not one the chosen Sprints carry
    Then nothing is sent, and the refusal names the ones they do
