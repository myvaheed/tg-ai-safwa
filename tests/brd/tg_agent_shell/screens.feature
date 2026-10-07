Feature: Screens
  A screen is a bot message the owner can act on: a dashboard, a Card editor, a review screen.
  Telegram is one column of messages, so a screen is only ever the last thing in the chat. What
  the owner does next is below it, and a screen they left behind is a control for a state that has
  moved on.

  Background:
    Given a chat where every screen the owner can act on carries its own buttons

  Scenario: SC-LIVE-001 — Only one screen is live, and it is the one the owner just acted on
    Given a dashboard, a Card editor and a review screen were all opened earlier in the conversation
    When the owner writes a message, runs a command, or walks into another screen from a menu
    Then every screen except the one that action belongs to stops being answerable
    And a dashboard or an editor is taken out of the chat, while a review screen stays as a written
      account of what became of it
    And the screen that action belongs to is left alone
    When the owner presses a button that only redraws the screen they are already on, such as
      turning its page
    Then no screen is ended by that

  Scenario: SC-KEEP-002 — What Safwa puts in the chat stays, is replaced, or takes itself away
    Given Safwa puts three kinds of thing in the chat: what is said, screens the owner acts on,
      and short notes about what just happened
    When the owner carries on
    Then what was said stays in the chat for good
    And a screen is redrawn in place, or taken out of the chat — never left standing beside a
      second copy of itself
    And a short note takes itself out of the chat after 5 seconds (TOAST_SECONDS = 5)

  Scenario: SC-BUTTON-003 — An action button works once
    Given a screen with action buttons on it
    When the owner presses the same action twice
    Then the work happens once, and the second press is told the action has expired

  Scenario: SC-BUTTON-003 — An action button dies with the run of Safwa that drew it
    Given a screen the owner can still see, drawn before Safwa was last restarted
    When they press an action on it
    Then nothing happens to the item it named
    And that screen is replaced by a line saying it is out of date, so nothing answerable is left
      standing

  Scenario: SC-BUTTON-003 — Getting to a screen is not an action, and never expires
    Given a button that only takes the owner somewhere — the menu, or the way back to it
    When they press it a second time, or press one drawn before the last restart
    Then the screen it names is drawn again
    And nothing about it can expire, because it names no item and changes nothing

  Scenario: SC-SPLIT-004 — A message too long for Telegram arrives whole, in several parts
    Given Safwa has more to say than 3900 characters fit (TELEGRAM_TEXT_LIMIT = 3900)
    When it puts that in the chat — an answer, a Summary, or the owner's own relayed words
    Then it arrives as several messages in order, and nothing is lost between them
    And the split falls at a line break where there is one
    And no bold, italic or code formatting is left open across a split
    And the conversation reads it back as one turn, not as several

  Scenario: SC-OPEN-006 — Opening an item means its real screen, not a copy of it
    Given the owner asked to see one item Safwa can point at
    When it is opened
    Then what arrives is the screen the owner would have reached by hand, buttons and all,
      and never a read-only retelling of it
    And every kind of item that can be cited opens the same way
    When a kind that is not one of them is asked for
    Then it is refused, rather than answered with an empty screen

  Scenario: SC-PAGE-007 — A list too long for one screen is offered a page at a time
    Given more rows than one screen holds
    When the owner opens that screen
    Then they get the first page, and it says which page of how many they are on
    And the way back is not offered on the first page, nor the way on from the last
    When they walk to the next
    Then nothing is left out: the last page holds what is left, however few

  Scenario: SC-INPUT-008 — A screen that asks for a typed value answers in place
    Given a screen asked the owner to type a value, showing what it holds now
    When they send one that is accepted
    Then it is kept, their message is taken out of the chat, and that same screen is redrawn in
      place rather than a second one appearing
    When what they send is refused
    Then nothing is kept, the message is still taken out of the chat, and the same screen says what
      was wrong and goes on waiting
    And a value the application has to read first, with a model or anything else slow, is read
      before anything is written, and what that reading refuses is shown the same way

  Scenario: SC-FAIL-005 — A review that could not be put on screen does not stay open
    Given Safwa prepared a change and the review screen for it could not be put in the chat
    When that happens
    Then the review ends rather than staying open with nothing on screen
    And the owner is told the change was not saved
    And what Safwa was waiting to say unasked — a Reminder that came due, a Sprint that ended — is
      said as soon as nothing of the owner's is open, rather than waiting for a restart

  Scenario: SC-PROGRESS-009 — A long job is watched on one message
    Given a job that reports how far it got as it goes
    Then one short note shows a bar of 10 cells (PROGRESS_CELLS = 10), the whole percent, and what
      the job is doing now
    And it appears with the first report, is redrawn in place only when what it would show changed,
      and is taken out of the chat when the job ends
    And it is never part of the conversation, by TG-KIND-002

  Scenario: SC-ALBUM-010 — A screen with photos shows them as one album above its words
    Given a screen shows photos above its words
    When it is drawn
    Then up to 10 photos arrive as one album, with its words and buttons below it
      (TELEGRAM_ALBUM_LIMIT = 10)
    And when the screen is replaced or taken away, its photos go with it

  Scenario: SC-CITE-011 — A photo Safwa points at opens as that photo
    Given Safwa's answer cites a photo by its label
    When the owner taps it
    Then Safwa sends that photo, under its description
    And it stays in the chat when the next screen comes, and is not part of the conversation

  Scenario: SC-BACK-012 — Back returns to the screen the owner came from
    Given a screen the owner reached from another screen, such as a Card opened from a Sprint list
    Then it offers ↩️ Back, and Back redraws the screen they came from, on the page they left
    And a screen opened from that one goes back to it the same way, up to 10 screens deep
      (NAV_DEPTH = 10), and the way back from the deepest one kept is the menu
    And a press that redraws a screen, such as turning its page or answering a Check, leaves
      its way back as it was
    When they open an item from a link in the chat, on the Home dashboard, or from the menu
    Then no screen stands behind it, and it offers ↩️ Menu in place of ↩️ Back

  Scenario: SC-LINK-013 — A link inside a screen opens what it names in place of that screen
    Given a screen whose words carry links, such as the Sprint's list or the plan's table
    When the owner taps one
    Then what it names is drawn in place of that screen, not as a message of its own
    And its ↩️ Back returns to that screen, on the same page and with the same filters
    And a link works once, as an action button does by SC-BUTTON-003, and a second tap says it is
      out of date
    When the owner taps 8 links inside 10 seconds (LINK_BURST_TAPS = 8, LINK_BURST_SECONDS = 10)
    Then they are told what is happening and that Telegram can stop opening bots for hours,
      because Telegram counts a link tap against the owner's account rather than the bot
    And the tap they just made still opens what it points at
