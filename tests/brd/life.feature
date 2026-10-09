Feature: Life in weeks
  Life in weeks is the owner's whole life as one picture: a square for every week, a row for
  every year, coloured by what Safwa recorded in it. Its way in is the Retro list, or asking
  Safwa for it in words.

  Numbers below name the constant they come from; the tests read the constant.

  Scenario: LF-OPEN-001 — Life in weeks is one tap from the Retro list
    Given the Retro list, whether or not a Sprint has ended
    Then it offers "⏳ Life in weeks"
    When the owner taps it
    Then the Life screen replaces the list: the owner's age and week of the year, the weeks
      lived, the day the records begin, and a button for each picture with something recorded
      to draw (LF-PAINT-004)
    And it leads to its Settings, back to the same page of the list, and to the menu
    When no birth date is set
    Then the screen says to set one in Settings, and offers no picture

  Scenario: LF-SET-002 — The birth date and the years of the grid are the owner's settings
    Given the Life screen
    When the owner opens Settings
    Then it shows the birth date, or that none is set, and how many years the grid holds, 90
      until the owner says otherwise (LIFE_YEARS_DEFAULT = 90)
    And each is typed in after a tap: the birth date as YYYY-MM-DD, a day before today and at
      most 120 years ago; the years a whole number from 50 to 120 (LIFE_YEARS_MIN = 50,
      LIFE_YEARS_MAX = 120)
    And a value that is refused keeps the prompt, says what to send, and changes nothing
    And a value that is taken redraws Settings saying what was updated, with the way back to
      the Life screen

  Scenario: LF-GRID-003 — A square for every week of life, a row for every year
    Given a birth date
    Then each row is one year of the owner's life, from one birthday to the next, in 52 squares
      (LIFE_WEEKS = 52); the 52nd takes the one or two days a year has over 52 weeks
    And the first square is the week the owner was born, and a column is the same weeks of the
      year in every row, so the months are written above the columns
    And a birthday on 29 February falls on 28 February in a year without one
    And the grid holds as many rows as Settings says, or one more than the owner's age when
      that is more
    And a day falls in the square of its local date in the workspace timezone
    And every week before the records begin is grey; they begin on the day Safwa was first
      started, or on the Diary's first day when that is earlier, and the grid marks that week
      with the date
    And the weeks after this one are empty, and this one has a ring and "You are here"
    And a week since the records began with nothing the picture counts is pale, and the legend
      says what it lacked

  Scenario: LF-PAINT-004 — Each picture colours the weeks by one thing
    Then Feeling: the mean of the Diary's feeling scores that week, from red at 0 to green at 10
    And Actions: how many Actions were finished that week, whether Effort Points were on or not
    And an Action that repeats counts each time it was finished, with its estimate each time
    And Effort Points, offered while they are on: the effort of the week's finished Actions
      that carry an estimate; a week whose finished Actions carry none is pale
    And Sprints: the Sprint that had most of the week's days, green when its Success criteria
      were met, red when not, grey when not marked and blue while it runs, with a mark on its
      first week; neighbouring Sprints alternate in depth, and a week no Sprint ran in is pale
    And Categories, Energy and Values: what the week's finished Actions carried (LF-SHARE-005)
    And a picture is offered only when something it counts was recorded
    And every number is counted by code; no model draws or counts anything

  Scenario: LF-SHARE-005 — Categories, Energy and Values show the mix of each week, or one share
    Given the picture by Category, by Energy type or by Value
    Then each week takes the colour of the one most of its finished Actions carried, a Category
      and an Energy type in their colours of RT-CHART-020, and the close-up stacks each week's
      mix
    And an Action serves a Value it carries, or one any of its ancestor Goals carries
    And an Action carrying two counts for both, and a week whose finished Actions carry none of
      them is pale
    And below the album: "All", then a button for each Category or Energy type the finished
      Actions carried, or for each of the 8 Values they served most (LIFE_VALUES_SHOWN = 8)
    When the owner taps one
    Then the album is drawn again with each week as deep as the share of its finished Actions
      that carried it, that share written in each square of the close-up, and the button ticked

  Scenario: LF-ALBUM-006 — A picture arrives as an album of the whole life and a close-up
    When the owner taps a picture on the Life screen
    Then one album replaces the screen: the whole grid, and a close-up of the years since the
      records began, with bigger squares and each week's number or mix in them
    And while it is drawn, the chat shows a photo being sent
    And a Value is named on the pictures without the characters the font cannot draw, such as
      emoji
    And below the grid a legend names each colour on it, a long name cut short, its keys apart
      from each other and inside the picture
    And the best week carries a star: the highest feeling, the most finished, or the highest
      share
    And the grid is headed by the picture, the owner's age and week, the weeks lived, the day
      the records begin, and what the picture adds up to (LF-STATS-007)
    And below the album one message names the picture and leads back to the Life screen and to
      the menu; going back takes the album out

  Scenario: LF-STATS-007 — The heading says what the picture adds up to
    Then Feeling: the mean over the weeks with a score, the best and the lowest week, and the
      last 4 weeks against the 4 before (LIFE_TREND_WEEKS = 4)
    And Actions: how many finished, in all and a week, the week with most, and the longest run
      of weeks with one finished
    And Effort Points: the effort finished, in all and a week, the week with most, and how many
      weeks had finished Actions with no estimate
    And Sprints: how many ended, in how many the Success criteria were met, the longest run met,
      and which day the running one is on
    And Categories or Energy, all of them: which one leads, with its Actions and share, which
      one is least, and how many weeks each one led
    And one Category or Energy type: its Actions and share of all, its highest week, the weeks
      without it, and the last 4 weeks against the 4 before
    And Values, all of them: the Value served most, with its Actions and share, the one served
      longest ago, and how many Values were never served
    And one Value: its Actions and the weeks they fell in, its highest week, when it was last
      served, and the longest gap

  Scenario: LF-ASK-008 — A picture asked for in words goes to the chat before Safwa's line
    Given a birth date is set
    When the owner asks Safwa for Life in weeks, or for one picture of it, of all or of one
      Category, Energy type or Value
    Then the album of LF-ALBUM-006 goes to the chat, with no message of words or buttons of its
      own, the chat showing a photo being sent meanwhile
    And asked for no picture by name, Safwa sends the first of LF-PAINT-004 with something
      recorded, and its line names the others
    And Safwa's answer follows the album, in one short line
    And the album stays in the chat as a message: the next screen does not take it out
    When a Category, an Energy type or a Value is asked for with another picture
    Then nothing is sent, and the refusal gives the call to make instead
    When the Value asked for has no such name, or the picture has nothing recorded to draw
    Then nothing is sent, and the refusal names the Values, or the pictures that have records
    When no birth date is set
    Then nothing is sent, and Safwa says to set one in Retro → Life in weeks → Settings
