# Cards: kinds, hierarchy and fields — approval packet

Status: **approved by the owner on 2026-08-23**, D1, D2 and D3 settled with it. The approved
scenarios are [`tests/brd/cards.feature`](../../tests/brd/cards.feature); the tests cite them from
there. See [Decisions](#decisions-the-owner-made).
Batch: Phase 5.a, the first of the Phase 5 packets
Sources: `CLAUDE.md` §Domain invariants, `archived_docs/INITIAL_PLAN.md` §"Cards form a strict tree"
and the two lines after it, `domain.create_card` / `validate_parent` / `validate_action_fields`,
`features/cards/proposal.py`, `telegram/cards.py`, and the tests listed in the audit.

Phase 5 is the board's centre and does not fit one packet. This one is what a Card **is**: its three
kinds, where each may sit in the tree, and which fields each kind may carry. The stage ladder, the
Checks that gate completion, repeat successors, the Sprint and the analytical projections are the
packets after it.

Twelve scenarios, `CD-KIND-001` … `CD-LINK-012`. Every number is written out with its constant named
next to it, and the tests read the constant — see [README.md](README.md#numbers).

## Scope

In: the three kinds and that a Card's kind is fixed once it exists; which parent each kind may take
and who may set it; the fields that belong to an Action alone; the title, the blocked reason, the
starting stage; and that a Card is created with all of its links or with none.

Out, and why:

- **`manual_stage` against `effective_stage`, and what a parent derives from its descendants.**
  Packet 5.b. **The block a Goal or an Idea shows for the Actions under it belongs there** — the
  owner settled in this packet that a Goal is never blocked in its own right, and a parent that
  shows a block derives it the same way it derives its stage. Here a Card only starts somewhere.
- **Checks as a completion gate, repeat successors, finishing and cancelling, archiving and deleting
  a subtree.** Packet 5.c. Archiving is a rule about a whole branch, not about one Card's shape.
- **The Sprint, its capacity and its metrics.** Packet 5.d.
- **Card progress, the dashboards and the analytical projections.** Packet 5.e.
- **Values, Tags and Checks as links.** Approved already: the fourteen `VL-` scenarios and the
  seven `TA-` ones.
  This packet only says that a Card's creation is refused whole when one of them cannot be linked
  (CD-LINK-012), which no approved scenario states.
- **The card screens as code.** Phase 8 moves the shared handler package whole. Their behaviour is
  in scope where a scenario names it.

---

## Scenarios

### CD-KIND-001 — A Card is a Goal, an Idea or an Action, and it stays the one it was created as

Status: approved
Sources: INITIAL_PLAN §"Cards form a strict tree"; `update_card_fields` (kind is not editable);
`CardToolInput.validate_target` (kind is not in the update set)

```gherkin
Given a Card exists as an Idea
When the owner or Safwa tries to make it an Action
Then the change is refused and the Card is still an Idea
And the only way to have an Action instead is to create one
```

### CD-TREE-002 — A Goal is always root-level

Status: approved
Sources: INITIAL_PLAN §"Goal is root-only"; `validate_parent`; `CardProposalHandler.prepare`

```gherkin
Given the owner has a Goal "Health"
When a Goal is proposed with a parent, or "Health" is moved under another Card
Then it is refused, and the refusal says a Goal is always root-level
And nothing about the Goal changes
```

### CD-TREE-003 — An Idea belongs to a Goal, or to no one

Status: approved
Sources: INITIAL_PLAN §"Idea may be root or a child of Goal"; `validate_parent`

```gherkin
Given a Goal "Health" and an Idea "Sleep better"
When "Sleep better" is placed under "Health"
Then it is placed there
When another Idea is placed under "Sleep better"
Then it is refused, and the refusal says an Idea may only be placed under a Goal
And an Idea under no parent at all is a Card in good standing
```

### CD-TREE-004 — An Action belongs to a Goal, an Idea or no one, and nothing belongs to an Action

Status: approved
Sources: INITIAL_PLAN §"Action may be root or a child of Goal or Idea; Action cannot have children"

```gherkin
Given a Goal, an Idea under it, and an Action "Buy a pillow"
When "Buy a pillow" is placed under the Goal, then under the Idea, then under nothing
Then each of the three is accepted
When any Card is placed under "Buy a pillow"
Then it is refused, and the refusal says an Action cannot have children
```

### CD-TREE-005 — A Card's place in the tree is only ever set by a proposal the owner saved

Status: approved — see D1
Sources: `telegram/cards.py` (no parent control on the creation screen or the Card screen);
`set_card_parent` has one production caller, `CardProposalHandler.apply`

```gherkin
Given the owner is looking at a Card
When they look for a way to move it under another Card, or to give it a child
Then there is none: the screens create, edit, link, and move a Card between stages
And the only path that changes a parent is a `card` proposal the owner saved
```

### CD-TREE-006 — A parent that is archived, or that is not there, is not a parent

Status: approved
Sources: `validate_parent`; `CardProposalHandler._resolve_parent_reference`

```gherkin
Given a Goal that the owner archived
When a Card is proposed under it, or an existing Card is moved under it
Then it is refused, and the Card keeps the parent it had
And a parent id that matches no Card is refused the same way
```

### CD-FIELD-007 — Effort, repeat, categories, energy and Blocked belong to an Action alone

Status: approved — Blocked was added to this list by the owner, see D2
Sources: INITIAL_PLAN §"Both can overlap and apply only to Actions" and §"An Action may be marked
Blocked only with a non-empty description"; CLAUDE.md §Domain invariants; `create_card`,
`sanitize_card_creation_state`, `CardProposalHandler.prepare`

```gherkin
Given a Goal is being written, by hand or as a proposal
When effort, repeat, a category, an energy type, or Blocked is set on it
Then the Goal is saved without them, rather than refused
And the screens never offer those controls for a Goal or an Idea
And the same holds for an Idea, and for a change to one that already exists
And a proposed change that was nothing but those fields is refused instead of saved empty
```

### CD-EFFORT-008 — An Action has to say how big it is, on the one scale

Status: approved
Sources: INITIAL_PLAN §"Action effort uses 1, 2, 3, 5, 8, or 13"; `validate_action_fields`

```gherkin
Given an Action is being written
When it is saved with no effort, or with a number off the scale
Then it is refused (EFFORT_POINTS = 1, 2, 3, 5, 8, 13)
And the Save button is not offered while the draft still has no effort
```

### CD-TITLE-009 — A Card has to be called something

Status: approved
Sources: `create_card`, `edit_card_text`, `update_card_fields`, `card_creation_errors`

```gherkin
Given a Card is being written or renamed
When the title is empty, or nothing but spaces
Then it is refused and the Card keeps the title it had
And a title that was saved with spaces around it is stored without them
```

### CD-BLOCKED-010 — A blocked Action has to say why, and unblocking takes the reason with it

Status: approved
Sources: INITIAL_PLAN §"only with a non-empty description"; `validate_blocked_fields`;
`edit_card_text`; `update_card_fields`

```gherkin
Given an Action is marked blocked
When it is saved with no reason written
Then it is refused, and the refusal says a blocked Action needs a reason
When the same Action is later unblocked
Then the reason goes with it, and the Action no longer shows a warning
```

### CD-STAGE-011 — A new Card starts in the Backlog and can never be born closed

Status: approved
Sources: INITIAL_PLAN §Stages; `create_card` (TERMINAL_STAGES refusal);
`sanitize_card_creation_state`

```gherkin
Given a Card is being written and no stage was chosen
Then it starts in Backlog
When a Card is written straight into Done or Cancelled
Then it is refused: a new Card starts in Backlog, Sprint or Today
```

### CD-LINK-012 — A Card is created with all of its Values, Tags and Checks, or the Card is not created at all

Status: approved
Sources: `create_card` (every Value, Tag and Check id is checked before the Card row is written);
`test_reviewed_card_creation_is_one_atomic_domain_write`

```gherkin
Given a Card is written with the Value "Health", the Tag "home" and the Check "Slept 7 hours"
When any one of those three has been archived, or no longer exists
Then no Card appears, and neither do the two links that were fine
And the owner is told which one could not be linked
And nothing is left half-written for them to clean up
```

---

## Decisions the owner made

Recorded 2026-08-23, in answer to the three questions this packet opened with.

### D1 — The tree is Safwa's to change, and the owner's never

`set_card_parent` has exactly one production caller, the Card proposal, and no screen offers a
parent control. That is deliberate: a parent or a child is set by asking Safwa, and CD-TREE-005 is
the rule, not a gap. Nothing is added to the screens.

### D2 — Blocked is an Action field

A Goal and an Idea are never blocked in their own right. Blocked belongs to an Action, exactly as
effort, repeat, categories and energy types do, so it joins them in CD-FIELD-007 and this packet
changes the code to match: today any kind can be blocked, on all three doors.

What a Goal or an Idea shows instead is **derived from the Actions in its branch**, the same way its
stage is. That derivation is not written here — it belongs with `effective_stage` in packet 5.b, and
this decision is recorded there as its source.

### D3 — The cycle guard is gone

`validate_parent` walked the ancestors to refuse a loop in the tree. No caller could reach it: a
Goal never takes a parent, an Idea only takes a Goal, an Action takes a Goal or an Idea and has no
children, so a Card under its own descendant cannot be asked for. The owner's rule: **do not write
or validate what cannot exist by definition** — no validator for the sun rising in the west.

The walk and the `card_id` parameter that existed only for it are deleted, and no scenario is
written for it. Removed in this batch rather than after approval, because it deletes unreachable
code and changes no behaviour a test can observe.

---

## Audit of the existing tests

| Scenario | Existing tests | Class | Decision | New tests |
|---|---|---|---|---|
| CD-KIND-001 | `test_ai_sql.py::test_card_tool_modes_reject_ambiguous_mutations` (partly: the update set) | business_valid | keep, cite | one for the domain door |
| CD-TREE-002 | `test_advisor_flow_e2e.py::test_ai_goal_proposal_reports_a_parent_instead_of_dropping_it` | business_valid | keep, cite | one for `validate_parent` |
| CD-TREE-003 | — | missing | — | one |
| CD-TREE-004 | `test_domain.py::test_committed_card_relationships_are_validated_propagated_and_audited` (partly) | business_valid | keep as VL-LINK-004, do not re-cite | one |
| CD-TREE-005 | `test_telegram_item_ui.py::test_manual_card_creation_uses_save_discard_and_no_parent_control` | business_valid | rename, cite | extend to the Card screen |
| CD-TREE-006 | `test_advisor_flow_e2e.py::test_child_proposal_fails_cleanly_when_earlier_parent_is_discarded` | business_valid | keep, cite | one for an archived parent |
| CD-FIELD-007 | `test_card_creation.py::test_goal_creation_programmatically_removes_action_only_fields` | business_valid | rename, cite | Blocked is new behaviour: one for each door |
| CD-EFFORT-008 | `test_card_creation.py::test_reviewed_card_creation_is_one_atomic_domain_write` (partly) | business_valid | keep for 012 | one |
| CD-TITLE-009 | — | missing | — | one |
| CD-BLOCKED-010 | `test_card_creation.py::test_blocked_card_requires_description`, `test_telegram_item_ui.py::test_card_text_and_blocked_reason_stay_on_one_validated_editor` | business_valid | rename to an Action, cite | — |
| CD-STAGE-011 | `test_card_creation.py::test_card_move_tool_rejects_a_terminal_stage` | business_valid | keep, cite | one for the default stage |
| CD-LINK-012 | `test_card_creation.py::test_reviewed_card_creation_is_one_atomic_domain_write` | business_valid | rename, cite | — |

`test_card_creation.py::test_card_field_updates_reject_an_unknown_priority`,
`test_user_item_timestamp_columns_are_timezone_aware` and `test_user_items_have_typed_timestamps`
stay green and uncited: the first is a contract detail of the enum, the other two are schema
characterization. Traceability fails on an approved scenario with no test, never on a test with no
scenario.

Any test that blocks a Goal or an Idea to set up something else has to become an Action. D2 makes
those setups invalid, and a test that keeps working after the rule changes was not testing the rule.

---

## What the code batch does after approval

Only after the scenarios above are approved and their tests are written and green.

**The behaviour change, D2.** Blocked becomes an Action field at every door: `validate_action_fields`
refuses it on a Goal or an Idea, `create_card` strips it with the rest, `ACTION_ONLY_FIELDS` in the
Card proposal gains `blocked` and `blocked_description`, and the creation and edit screens stop
offering the toggle for a non-Action. **This moves `SYSTEM_PROMPT`**, because the Advisor's prompt
carries "Only Actions have effort, repeatability, categories, energy" and Blocked has to join that
line — the batch declares that hash, and `PERSONA` and every tool schema must stay untouched beside
it. `BOARD_PROMPT` moves for the same sentence.

**The move.**

- `features/cards/model.py` takes `Card`, `CardCategory`, `CardEnergyType`, `CardCheck`,
  `CardEvent`; `safwa.models` keeps the compatibility imports so startup still sees the whole
  metadata. No column changes, so `schema.json` comes out byte-identical.
- `features/cards/use_cases.py` takes `create_card`, `edit_card_text`, `update_card_fields`,
  `set_card_parent`, `validate_parent`, `validate_action_fields`, `validate_blocked_fields`,
  `card_snapshot` and the category and energy toggles.
- `EFFORT_POINTS`, `TERMINAL_STAGES` and `LIVE_STAGE_PRECEDENCE` move next to the code that uses
  them, per CLAUDE.md: a limit that belongs to one feature is a constant in that feature.
  `ai/contracts.py` mirrors the effort scale in a `Literal` and moves with it when
  `ai/contracts.py` is split.
- The card screens stay in the shared `telegram` package. Phase 8 moves the handlers whole — the
  Phase 4 rule that a per-feature screen move costs three deferred imports has not changed.

`domain.py` is expected to fall by roughly 300 lines. The Checks, Sprint and archive code moves in
5.c and 5.d, and `domain.py` is deleted at the end of the phase or it is not deleted at all.
