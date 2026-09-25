Feature: Onboarding
  Safwa explains itself as the owner works: one notice, a tip from the onboarding subagent after
  each created or finished item, and the same subagent answering questions about Safwa and
  proposing to stop the onboarding.

  Numbers below name the constant they come from; the tests read the constant.

  Scenario: OB-NOTICE-001 — Safwa's first turn is preceded by the notice, and only the first
    Given onboarding is on and no onboarding notice was ever sent to this chat
    When Safwa's first turn begins — on the owner's word or on its own initiative
    Then before the answer comes one notice: tips will come, ask anything, say stop to end it
    And it is registered as Safwa's own words, and the model reads it so in later turns
    When any later turn begins, or Safwa restarts, or onboarding is switched off and on
    Then no second notice comes
    When the owner's message arrives while a turn of Safwa's own is still checking whether the
      notice was sent
    Then that turn puts nothing in the chat, and the owner's own turn is what carries the notice
    When onboarding is off
    Then no notice comes at all

  Scenario: OB-TIP-002 — A created or finished item earns a tip from the onboarding subagent, however it was saved
    Given onboarding is on
    When a Value is saved — from a proposal or by hand on the Values screen
    Then once the chat is free, the Advisor is asked to route to onboarding, told that a Value
      was created, with that Value cited
    And the tip reaches the owner as the subagent wrote it, as a block of its own at the top of
      the message, with the Advisor's words after it, which do not repeat it
    When several items are saved while the chat is busy
    Then they are handed to the subagent together — at most 5 cited, the rest counted
      (ONBOARDING_TIP_ITEMS = 5) — and one message comes
    When the Advisor answers without routing to onboarding
    Then the tip is lost and not asked for again

  Scenario: OB-TIP-003 — Tips wait behind the owner's own work, and beside other reactions
    Given a tip is owed and a proposal screen is open
    Then the tip waits until the screen is answered
    Given a tip and a blocker question are owed at the same time
    Then the tip comes as its own block and the blocker's question is still asked, below it

  Scenario: OB-ASK-004 — Questions about Safwa are answered in the subagent's own words
    Given the owner asks what a Check is, or what Safwa can do
    When the Advisor routes to onboarding
    Then the explanation reaches the owner as the subagent wrote it, paragraphs intact, with
      the Advisor's words after it, which do not repeat it
    And it is answered whether onboarding is on or off
    Given the owner asks to explain Checks and to add one
    Then the Check is proposed on its screen, and the explanation comes in the answer once the
      screen is answered
    And the explanation is not handed to the workspace subagent as something already saved

  Scenario: OB-STOP-005 — Stopping onboarding is a proposal that an unambiguous request saves itself
    Given onboarding is on
    When the owner's newest message says they do not want it
    Then the onboarding subagent proposes to turn it off, and it is saved with no screen when
      the reviewer is available and agrees, by PR-AUTO-024
    And it is off through the same switch as the Profile's, the tips still owed are dropped, and
      nothing on the workspace changed
    When the words leave any doubt, or the reviewer is unavailable
    Then the proposal takes the Save/Discard screen instead
    When the proposal is made in a turn Safwa took on its own, where no owner message asked for it
    Then it takes the Save/Discard screen, never the automatic save
    When onboarding is already off
    Then the proposal is refused, and the Advisor says the switch is in the Profile

  Scenario: OB-MANUAL-006 — The manual names only what exists
    Given the subagent's instructions name commands, menu buttons, routes and the Profile's
      switches
    Then each of them is registered by the application
    And the menu and the switches it lists are the whole of them

  Scenario: OB-RETURN-007 — The owner's first message after 14 days away is followed by what still stands
    Given the owner's previous message, typed or spoken, is 14 days old or older
      (RETURN_AFTER_DAYS = 14)
    And Safwa wrote on its own in between
    When the owner writes
    Then Safwa answers that message first
    And once the chat is free, one more message comes that opens with how many days it has been
      and the Actions in Today and in the Sprint, each marked Today or Sprint, under its Goal,
      and under its Subgoal when it has one, every Card cited
    And Actions with no Goal stand in a group of their own
    And below the list the Advisor is asked to find out which of them still matter, and to offer
      a new Sprint when none is running
    When more than 30 Actions stand there (RETURN_LIST_ACTIONS = 30)
    Then 30 are listed and the rest are counted
    When Today and the Sprint hold nothing
    Then the message says so, and the Advisor is asked to offer planning
    When the owner's previous message is less than 14 days old, or there is none
    Then no such message comes
