# Values and Tags — approval packet

Status: **approved by the owner on 2026-08-23**, with Q1 through Q5 settled alongside it; Q5 as
option C, which grows the batch.
Batch: Phase 4.d

Sources: `archived_docs/INITIAL_PLAN.md` §Cards and §Advisor context, `archived_docs/ARCHITECTURE.md`
§Card links, the current code and its tests, and the owner's decisions recorded below.

**How `archived_docs/` is read.** It stays a source while the migration runs. But it was written
quickly through chats and carries wrong artifacts, so a line from it is not copied into a scenario
word for word. Read what it is getting at, check it against the code, and write that in plain
language. Where the two disagree, the disagreement ships as a question.

**Plain language.** A `.feature` has to read cleanly for someone who has never seen the code, and
that applies to the files already approved as well. `docs/brd/README.md` gained a `## Language`
section with the rule, and all five existing `.feature` files were rewritten to it in this batch —
same scenarios, same identifiers, same order, different words.

Two feature files, because the two entities are two different businesses:

- `tests/brd/values.feature` — a Value is a focus. PL-VALUE-001 … PL-VALUE-014.
- `tests/brd/tags.feature` — a Tag is a label for finding Cards. PL-TAG-015 … PL-TAG-021.

Twenty-one scenarios. That is past the five-to-fifteen guideline, and it is Q5 that put it there:
fourteen of them are the move, seven are what a Check carrying a Value brings with it. The two files
are what keeps it reviewable — Tags can be read on its own in a minute, and Values is the only one
that needs the Check rules held in mind.

Numbers are written out with the constant named next to them, and the tests read the constant — see
[README.md](README.md#numbers).

**Prefix.** `PL`, the Planning prefix, not one of their own. Planning is not split into artificial
bounded contexts, so Values and Tags are batches of Planning rather than features beside it. `001`
through `021` are taken here; the Phase 5 Cards, Checks and Sprint packets continue from `022`.

## What the owner changed while reading the drafts

- **"Focus is the only thing a Value has that a Tag does not."** Cut. Do not tie the two together
  and do not fence them off from each other. A Value has a focus. That is all it needs to say.
- **"Both doors write the same Value or Tag."** Cut. Obvious.
- **"Neither classifies a Check."** Cut, then reversed: a Check *should* carry Values. Q5, settled
  as C, and it is the largest thing in this batch.
- **Autoapproval.** It came up in three packets in a row — Requests, Reminders, and here. It is one
  mechanism, so what may and may not be saved without a screen belongs in one place, written once.
  The old `PL-AI-012` is withdrawn and the whole allowlist — every entity, every action, including
  the answer to Q1 and the new Check link — goes to the Proposals and autoapproval packet.
- **Two feature files**, split on business purpose rather than on shared plumbing.
- **An answered repeat must let go of its Values**, or every cycle would copy them and a Value would
  end up holding a pile of finished Checks. That is PL-VALUE-012, and the owner named the same
  question for a repeatable Card — that one is Phase 5's.

## Scope

In: what a Value is and what focus means, what a Tag is and what it is for, naming, reviving an
archived name, archiving and its unlink, linking Values and Tags to Cards, **linking Values to
Checks**, reading a Value from both directions, and choosing them on a Card.

Out, and why:

- **Everything else the Card owns.** `toggle_card_value` and `toggle_card_tag` write a Card link:
  they load the Card, snapshot it, record a Card event and bump the Card's version. They are Card
  writes that happen to name a Value, and Phase 5 owns them. This batch says what a link may point
  at; it does not move the toggles.
- **A repeatable Card and its Values.** The same question PL-VALUE-012 answers for a Check. The
  owner named it and put it in Phase 5, where repeats live.
- **`resolve_references`.** It turns names and numbers into items and knows nothing about who is
  linking to them. Unchanged, and it stays in `domain.py` for Phase 5.
- **Autoapproval.** Moved out, see above. The new Check link is not on the allowlist, so it always
  goes to the owner — that is the default, not a decision this batch makes.
- **Everything else about Checks.** What a Check is, answering one, the repeat successor itself, and
  the completion gate are Phase 5. This batch gives a Check one new thing it can carry, and says
  what happens to that thing when the Check is answered, archived or deleted.
- **Citations and `open`.** One rule over every item type at once; a later packet owns it.
- **The screens as code.** `telegram/items.py`, `telegram/commands.py`, `telegram/cards.py`,
  `telegram/checks.py` and `telegram/callbacks.py` stay in the shared package — moving a screen into
  its feature is what made `features/profile/screens.py` an import cycle, and Phase 8 moves them
  together. Their *behaviour* is in scope.
- **The text-input editor.** The Name and Description prompts reuse `text_input.py`, which has its
  own rules and its own test.

`ReferenceSpec` was out of scope in an earlier draft and is now **touched but not moved**: it
assumes the thing doing the linking is a Card, and a Check linking to a Value needs that to be a
parameter. See the code plan.

---

## `tests/brd/values.feature` — a Value is a focus

### PL-VALUE-001 — A Value carries a focus, and the owner turns it on and off

Status: approved

```gherkin
Given a Value the owner has written down
Then it also carries a focus, which is on or off
And the Value's screen has a button that flips it, showing which way it is now
And /values shows at a glance which Values are in focus
```

### PL-VALUE-002 — Values in focus are the ones Safwa is told to weigh

Status: approved

```gherkin
Given "Fitness" is in focus and "Tidiness" is not
When Safwa answers the owner
Then Safwa has been told Fitness is in focus, and not Tidiness
And the Value arrives as something Safwa can hand straight back to the owner as a link
And a Value the owner archived is not mentioned at all
```

### PL-VALUE-003 — A critical Card that serves a Value in focus is shown to Safwa first

Status: approved

```gherkin
Given the owner has more critical Cards than Safwa is handed (CONTEXT_CRITICAL_CARD_LIMIT = 10)
When they are picked
Then a critical Card that serves a Value in focus comes before one that does not
And a Card that only serves an archived Value does not count as serving a focus
And among the rest, a Card with a Hard Time comes first, and then the older one
```

### PL-VALUE-004 — One Value is carried by many things, which is what it is for

Status: approved

```gherkin
Given a Value named "Health"
When the owner puts it on a morning run, a dentist appointment and a Goal
Then all three carry Health, and Health is carried by all three
And taking Health off one of them leaves it on the other two
And the Value's screen says how many Cards and how many Checks carry it right now
```

### PL-VALUE-005 — A Value's name is taken whatever the capitals, and is never blank

Status: approved

```gherkin
Given a Value named "Fitness"
When a second Value is written as "FITNESS"
Then it is refused, because that name is taken
And renaming another Value to "fitness" is refused the same way
And a name that is empty, or only spaces, is refused
```

### PL-VALUE-006 — Writing down a Value the owner archived brings that one back

Status: approved

```gherkin
Given a Value named "Fitness" was archived, and it had a description
When a Value is written down under that name again, in any capitals
Then it is the archived one that comes back, still the same Value
And it keeps the description it had, unless a new one was given
And a description nobody typed is not a new one
And it comes back with its focus off, and on nothing, because archiving took it off everything
And there is still only one Fitness
```

### PL-VALUE-007 — Archiving a Value takes it off everything, and the things it was on stay

Status: approved

```gherkin
Given a Value in focus, carried by Cards and by Checks
When the owner archives it
Then they are asked first, and told how many Cards and Checks it is about to come off
And it comes off all of them at the moment it is archived
And those Cards and Checks are otherwise untouched: none of them is deleted, moved or changed
And the Value's focus is off
And a Value that is already archived cannot be archived again
```

### PL-VALUE-008 — A Value is archived, never deleted

Status: approved

```gherkin
Given something wants a Value gone
Then archiving is the only way it goes
And a request to delete one is refused before anything is written
And the owner's screen offers Archive and no delete either
```

### PL-VALUE-009 — Nothing can be linked to a Value that is archived

Status: approved

```gherkin
Given a Value the owner archived
When something tries to put it on a Card or a Check, by its name or by its number
Then it is refused
And a name that belongs to no Value is refused too, and says which name it was
And nothing is half-done: if one name in a link cannot be found, none of them are linked
```

### PL-VALUE-010 — A Check can carry a Value, because a Check shows how well that Value is held to

Status: approved — new behaviour, decided by Q5

```gherkin
Given a Value named "Health" and a Check "Did I sleep seven hours?"
When the owner puts Health on that Check
Then the Check carries Health, and Health is carried by that Check
And the link is put on and taken off from the Check
And a Check's Values and a Check's Cards have nothing to do with each other: putting a Value on a
  Check changes nothing about the Cards that Check belongs to, and their Values are not its own
```

### PL-VALUE-011 — Archiving a Check keeps its Values; deleting one takes them away

Status: approved — new behaviour, decided by Q5

```gherkin
Given a Check carrying a Value
When that Check is archived
Then it still carries the Value, so it has it again if it comes back
When that Check is deleted instead
Then the link goes with it, and the Value is not left pointing at something that is gone
```

### PL-VALUE-012 — An answered repeat hands its Values to the copy that takes its place

Status: approved — new behaviour, decided by Q5

```gherkin
Given a repeatable Check carrying a Value
When it is answered, Passed or Missed
Then the fresh copy that takes its place carries that Value
And the answered one no longer does
And so a Value is carried by the Check the owner is still answering, and never by a pile of
  finished ones
But a Check that does not repeat keeps its Values when it is answered, because it is the record of
  that one observation
```

### PL-VALUE-013 — Safwa can see which Values a Check is about

Status: approved — new behaviour, decided by Q5

```gherkin
Given Checks carry Values
When Safwa looks at a Check
Then it sees the Values on it, by name
And that is how it knows which Value the owner is working on through that Check
```

### PL-VALUE-014 — Safwa can start from a Value and find what is behind it

Status: approved — new behaviour, decided by Q5

```gherkin
Given a Value named "Health"
When Safwa is asked how Health is going
Then starting from the name Health it can find the Checks that carry it, and the Cards that carry it
And the Checks are the evidence: they say how well Health is actually being held to
And the Cards are the work: they say what is being done about it
And an archived Card or Check is in neither answer
```

---

## `tests/brd/tags.feature` — a Tag is a label for finding Cards

### PL-TAG-015 — A Tag is a label, and one Tag is on many Cards

Status: approved

```gherkin
Given a Tag named "Family"
When the owner puts it on a phone call, a trip plan and a birthday
Then all three carry Family, and Family is on all three
And that is what a Tag is for: finding those Cards together later
And taking Family off one of them leaves it on the other two
And the Tag's screen says how many Cards carry it right now
And a Tag has no focus, and a Tag never goes on a Check — a Tag is for finding Cards
```

### PL-TAG-016 — A Tag's name is taken whatever the capitals, and is never blank

Status: approved

```gherkin
Given a Tag named "Family"
When a second Tag is written as "FAMILY"
Then it is refused, because that name is taken
And renaming another Tag to "family" is refused the same way
And a name that is empty, or only spaces, is refused
```

### PL-TAG-017 — Writing down a Tag the owner archived brings that one back

Status: approved

```gherkin
Given a Tag named "Family" was archived, and it had a description
When a Tag is written down under that name again, in any capitals
Then it is the archived one that comes back, still the same Tag
And it keeps the description it had, unless a new one was given
And a description nobody typed is not a new one
And it comes back on no Cards, because archiving took it off the ones it was on
And there is still only one Family
```

### PL-TAG-018 — Archiving a Tag takes it off its Cards, and those Cards stay

Status: approved

```gherkin
Given a Tag that is on some Cards
When the owner archives it
Then they are asked first, and told how many Cards it is about to come off
And it comes off all of them at the moment it is archived
And those Cards are otherwise untouched: none of them is deleted, moved or changed
And a Tag that is already archived cannot be archived again
```

### PL-TAG-019 — A Tag is archived, never deleted

Status: approved

```gherkin
Given something wants a Tag gone
Then archiving is the only way it goes
And a request to delete one is refused before anything is written
And the owner's screen offers Archive and no delete either
```

### PL-TAG-020 — A Card cannot be given a Tag that is archived

Status: approved

```gherkin
Given a Tag the owner archived
When something tries to put it on a Card, by its name or by its number
Then it is refused
And a name that belongs to no Tag is refused too, and says which name it was
And nothing is half-done: if one name in a link cannot be found, none of them are linked
```

### PL-TAG-021 — Choosing Tags for a Card shows them a page at a time

Status: approved

```gherkin
Given a Card is being tagged and there are more Tags than fit on a page
When the list of Tags opens
Then it shows 10 of them and says which page this is (SELECTOR_PAGE_SIZE = 10)
And Next reaches the rest, so no Tag is out of reach
And ticking one on the second page leaves the owner on the second page
And Back returns to the Card the list was opened from
```

The same list is used for choosing Values, and Checks, and Categories, and Energy types — one
screen. The rule is written once, here, because a Tag is the thing that screen exists for. Phase 5
can widen it when it takes the Card screens.

---

## Questions

### Q5 — Should a Check be linkable to a Value?

**Settled: yes, and in this batch.** The owner asked for it now rather than in Phase 5.

The concern was stated and the owner reaffirmed, so it is recorded rather than argued: this turns a
file move into a feature. **The batch is still worth doing as one**, because the Value half of the
archive rule (PL-VALUE-007) has to know about Checks the moment the link exists, and that rule is
this batch's — splitting them would mean shipping a Value archive that leaves the Value pointing at
Checks it no longer belongs on.

**Why the link is worth having.** A Card is work that *serves* a Value. A Check is an observation
that shows *how well the Value is actually being held to* — "did I sleep seven hours?" says
something about Health without being work at all. The two are separate statements, so nothing is
derived from one to the other: a Check's Values are its own, and the Values of the Cards it belongs
to are theirs. PL-VALUE-010 says that in one line so nobody has to guess.

**Which side attaches it.** The Check. Every link Safwa has works this way already — a Card carries
its own Values, Tags and Checks, and the Card is where they go on and come off. A Check carrying a
Value is the same shape one level down. The `card` tool keeps putting Checks on Cards; the `check`
tool puts Values on a Check.

**Answered repeats.** A repeatable Check spawns a fresh copy when it is answered, and that copy
takes the Card links with it. Values have to work the same way or worse: if both the answered Check
and its successor kept the Value, a Value would gain one dead Check per cycle forever. PL-VALUE-012
hands the Values to the successor and takes them off the answered one, so a Value is carried by the
Check the owner is still answering. A one-off Check keeps its Values, because it *is* the record.

**A Tag on a Check was not asked for and is not added.** A Tag is for finding Cards. PL-TAG-015 says
so out loud, so that adding one later is a decision rather than a drift.

What Q5 costs, so nothing is a surprise later:

| | |
|---|---|
| A new table | `check_values`, one row per link |
| `ReferenceSpec` | gains the owner's column as a parameter; it hardcodes `card_id` today |
| The `check` tool | gains `link` and `unlink` modes with `value_id` / `value_ids` / `value_query` |
| `ai_checks` | gains a `values` column, comma-joined names, like `ai_cards.direct_values` |
| Answering a repeat | `_copy_check` carries the Values over, and the answered one lets them go |
| Deleting Cards | `delete_subtree` must clear `check_values` for the Checks it deletes |
| The Check screen | gains a Values picker; the Check proposal screen has to show them |
| The Value screen | counts Checks as well as Cards |
| **Snapshots** | **four hashes move, all declared** — see Gates |

### Q1 — May a Value's focus be turned on without a review screen?

**Settled: yes, unchanged** — but the rule does not live here. Autoapproval is one mechanism and gets
one packet; this answer is recorded there with every other entity's. What was asked and answered:
`("value", "update")` allows `{name, description, active}`, so a proposal whose only change is the
focus can be saved on the reviewer's word alone, with a receipt and no screen. The owner kept it,
because the reviewer's own criterion is already "every changed field and its exact new value are
clearly requested".

### Q2 — What happens to `effective_value_ids`, which nothing calls?

It walks a Card's whole subtree and returns every Value found in it — "a Goal carries the Values of
everything under it". It has **no production caller**; its only callers are two assertions in
`test_domain.py`. No screen shows it, no view exposes it, and Safwa cannot reach it.

**Settled: delete it**, with the two assertions holding it up.
`test_parent_effective_values_are_derived_from_descendants` goes whole — it asserts nothing else —
and `test_committed_card_relationships_are_validated_propagated_and_audited` loses one line.

### Q3 — Writing down an archived name from the screen wiped its description

Two doors, two answers, and the difference was silent. Safwa's door sends nothing when it has nothing
to say, and the stored description survives. The owner's door could not say "nothing": the
description box starts empty and that empty string overwrote. An owner who typed the name of a Value
they archived last month got it back with its description erased — and the screen never showed them
the old description, so nothing on it looked like a deletion.

**Settled: fix the screen.** A box the owner never typed into is not something they gave. One line
in `_on_item_create`. PL-VALUE-006 and PL-TAG-017 both carry the line, and their tests fail first.

### Q4 — Ticking a Tag on page 2 threw the owner back to page 1

The list reopened after every tick without its page, so it always redrew page 1. With eleven Tags,
putting the last two on a Card meant page forward, tick, page forward, tick. The paging existed and
undid itself.

**Settled: keep the page.** The list reopens where the owner was. The fix is in the shared handler,
so Checks, Categories and Energy types get it too; PL-TAG-021 claims the Tag half. Fails first.

---

## Audit table

`business_valid` means the test agrees with the scenario above it and needs only its docstring and,
where the module moves, its import path.

| Scenario | Existing tests | Class | Decision | Status |
|---|---|---|---|---|
| PL-VALUE-001 | `test_domain.py::test_ui_mutations_use_domain_services_and_are_audited` (flips focus) | business_valid | keep, cited; **to write**: the Focus button is on the Value screen, and `/values` shows which are in focus | approved |
| PL-VALUE-002 | — | missing | **to write**: the context line over a Value in focus, one out of focus, and an archived one | approved |
| PL-VALUE-003 | — | missing | **to write**: the ordering of the critical Cards Safwa is handed | approved |
| PL-VALUE-004 | `test_domain.py::test_committed_card_relationships_are_validated_propagated_and_audited` | business_valid | keep, cited; **to write**: one Value over three Cards, and both counts on its screen | approved |
| PL-VALUE-005 | `test_domain.py::test_create_tag_and_value_restore_archived_names` (its tail refuses a duplicate) | business_valid | keep, cited; **to write**: the rename refusal and the blank name | approved |
| PL-VALUE-006 | `test_domain.py::test_create_tag_and_value_restore_archived_names` | business_valid | keep, cited; extend with "on nothing". Q3's half **to write**, fails first | approved |
| PL-VALUE-007 | `test_telegram_item_ui.py::test_manual_tag_and_value_archive_unlinks_cards` | business_valid | keep, cited; **to write**: the Check links come off too | approved |
| PL-VALUE-008 | `test_ai_sql.py::test_change_from_tool_maps_modes_to_actions` (the archive-never-delete half) | business_valid | keep, cited | approved |
| PL-VALUE-009 | `test_domain.py::test_committed_card_relationships_are_validated_propagated_and_audited` (the unknown-number half) | business_valid | keep, cited; **to write**: an archived Value by name and by number, from a Card and from a Check | approved |
| PL-VALUE-010 | — | missing | **to write**, fails first: the link from the Check side, and that a Check's Values and its Cards' Values are separate | approved |
| PL-VALUE-011 | — | missing | **to write**, fails first: an archived Check keeps its Values, a deleted one takes them away | approved |
| PL-VALUE-012 | `test_checks.py` covers the repeat successor and its Card links | business_valid | keep, uncited — it is the repeat rule, Phase 5's; **to write**, fails first: the Values go to the successor and off the answered one, and a one-off keeps them | approved |
| PL-VALUE-013 | — | missing | **to write**, fails first: the new `ai_checks` column, read the way Safwa would read it | approved |
| PL-VALUE-014 | `test_advisor_flow_e2e.py::test_ai_request_query_values_and_ignores_archived_cards` (the Cards half, through a Request) | characterization_valid | keep, uncited; **to write**, fails first: from one Value name to its Checks and its Cards, archived ones absent | approved |
| PL-TAG-015 | `test_advisor_flow_e2e.py::test_ai_create_tag_and_links_are_reviewed_as_separate_proposals` (one Tag over two Cards) | business_valid | keep, cited; **to write**: the count on the Tag screen, no focus control, and no way to put one on a Check | approved |
| PL-TAG-016 | `test_domain.py::test_create_tag_and_value_restore_archived_names` | business_valid | keep, cited; **to write**: the rename refusal and the blank name | approved |
| PL-TAG-017 | `test_domain.py::test_create_tag_and_value_restore_archived_names` | business_valid | keep, cited; Q3's half **to write**, fails first | approved |
| PL-TAG-018 | `test_telegram_item_ui.py::test_manual_tag_and_value_archive_unlinks_cards` | business_valid | keep — but one test cannot cite two scenarios, so PL-VALUE-007 takes it and this one is **to write** over a Tag | approved |
| PL-TAG-019 | `test_ai_sql.py::test_change_from_tool_maps_modes_to_actions` | business_valid | keep — PL-VALUE-008 takes it; this one is **to write** over a Tag | approved |
| PL-TAG-020 | — | missing | **to write**: an archived Tag by name and by number, and a name that is nobody's | approved |
| PL-TAG-021 | `test_telegram_item_ui.py::test_tag_selector_pages_instead_of_truncating` | business_valid | keep, cited — it hard-codes `10` and `12`; rewrite to read `SELECTOR_PAGE_SIZE`. Q4's half **to write**, fails first | approved |

**Ten gaps that exist and nothing checks, and seven tests that fail first.** The gaps are behaviour
the code already has: the Focus button, the context line, the critical-Card ordering, both rename
refusals and blank names, one Value and one Tag over many Cards with their counts, an archived
reference in a link for both, and the Tag halves of archive-and-unlink and archive-never-delete. The
seven that fail first are Q3 (twice), Q4, and the five new Check rules — PL-VALUE-010 through
PL-VALUE-014.

Tests in these files that belong elsewhere and are untouched:
`test_telegram_item_ui.py::test_tag_field_input_reuses_editor_message_and_deletes_input` (the text
editor), `::test_item_proposal_shows_diffs_and_only_save_discard_footer` (the item proposal screen),
`::test_open_item_screen_renders_the_manual_screen_of_every_item` and the `::test_citations_*` family
(citations), `test_advisor_flow_e2e.py::test_new_tag_and_dependent_card_link_use_one_repair_round`
(proposal repair rounds), `::test_ai_approved_tag_proposal_creates_a_reusable_tag` and
`::test_ai_create_value_and_link_are_reviewed_as_separate_proposals` (the proposal path), and every
`test_autoapproval_e2e.py` test (moved to the autoapproval packet). All stay green and uncited.

---

## The code plan, for approval alongside the behaviour

Two halves: the move, which changes nothing, and Q5, which is new behaviour.

### The move

**Values and Tags go into `features/planning/`, not into a package of their own.** Planning keeps
Card, Check, Value, Tag and Sprint together. This batch creates `features/planning/model.py` and
`features/planning/use_cases.py` — files the feature does not have yet — and puts only the Value and
Tag half in them. Phase 5 fills the same two files with Cards, Checks and the Sprint.

| From | To | Note |
|---|---|---|
| `models.Value`, `models.Tag`, `models.CardValue`, `models.CardTag` | `features/planning/model.py` | `safwa.models` keeps the compatibility imports, as Diary, Reminders and Requests did. No column type changes — a move that alters the declared schema is not a move |
| `domain.create_value`, `update_value_fields`, `archive_value`, `set_value_focus` | `features/planning/use_cases.py` | the caller keeps owning the transaction |
| `domain.create_tag`, `update_tag_fields`, `archive_tag` | `features/planning/use_cases.py` | |
| `domain.effective_value_ids` | **deleted** (Q2) | with its two assertions |

`utcnow()` becomes `datetime.now(UTC)`, as it did in 4.b.1 and 4.c.

**What stays put.** `toggle_card_value`, `toggle_card_tag`, `resolve_references`, `VALUE_REFERENCE`,
`TAG_REFERENCE` and `CARD_REFERENCE_SPECS` stay in `domain.py`. The `/values` and `/tags` commands,
`telegram/items.py`, `telegram/checks.py` and the item callbacks stay in the shared package for
Phase 8. `SELECTOR_PAGE_SIZE` and `CONTEXT_CRITICAL_CARD_LIMIT` stay in `constants.py`.

### Q5: the Check link

| What | Where |
|---|---|
| `CheckValue(check_id, value_id)` | `features/planning/model.py`, next to `CardValue` |
| `toggle_check_value` | `domain.py`, beside `toggle_card_value` — it is a Check write, and Checks are Phase 5 |
| `ReferenceSpec.owner_key` | defaults to `"card_id"`; the Check spec passes `"check_id"`. `link_key` and `link_column` read it instead of hardcoding the Card |
| `CHECK_VALUE_REFERENCE` | `domain.py`, with the other specs |
| `archive_value` | also clears `check_values`, in the same transaction (PL-VALUE-007) |
| `_copy_check` | copies the source Check's Values onto the successor, and the answered Check loses them (PL-VALUE-012) |
| `delete_subtree` | also clears `check_values` for the Checks it deletes (PL-VALUE-011) |
| `CheckToolInput` | gains `link` and `unlink` modes and the three `value_*` fields |
| `CheckProposalHandler` | applies the link the way `CardProposalHandler` does |
| `CheckProposalPresenter` | shows the Values on the review screen |
| `AI_CHECKS` view | gains `values`, comma-joined names, like `ai_cards.direct_values` |
| `SYSTEM_PROMPT` and `BOARD_PROMPT` | the `ai_checks` column list is prose in both and gains the column |
| `telegram/checks.py` | the Check screen gains a Values picker, through the existing `RelationChoice` shape, which already carries `link_owner` |

`resolve_references` is untouched: it turns names and numbers into Values and does not care who is
linking. That is why `ReferenceSpec` only needs the owner column.

PL-VALUE-014 needs no new column. Once `ai_checks` has `values`, going from a Value's name to what
carries it is `ai_cards.direct_values LIKE '%Health%'` and `ai_checks.values LIKE '%Health%'` — the
same shape the prompt already tells the model to use, and both views already hide archived rows.

**Two tools that touch Checks, and why that is right.** The `card` tool puts a Check on a Card; the
`check` tool puts a Value on a Check. Each link is written from the side that carries it, which is
the rule Safwa already follows everywhere. Both tool descriptions say which one they are, in one
line, because a small local model is the reader.

### The two behaviour fixes, which move nothing

Q3 is one line in `telegram/callbacks.py` — `_on_item_create` passes nothing for a box the owner
never typed into. Q4 is the page carried through the tick handler back into the list.

Callers that change import path only: `domain.py`, `features/planning/{proposal,telegram}.py`,
`ai/context.py`, `ai/service.py`, `telegram/{_core,callbacks,commands,items}.py`, and the tests.

Expected: `domain.py` falls by roughly 165 lines net — about 185 out for the move and
`effective_value_ids`, about 20 back in for `toggle_check_value`, the new spec and the successor's
Values. No new dispatch point, no use-case base, no cycle.

---

## Gates

| | Baseline (4.c) | This batch |
|---|---:|---:|
| Tests | 560 passed / 3 skipped | 582 passed / 3 skipped |
| `ruff check .` | clean | clean |
| Entity dispatch points outside `features/` (DoD #1) | 28 | 28 |
| Use case base abstractions (DoD #2) | 0 | 0 |
| Modules over 600 lines (DoD #3) | 5 | **6** |
| Re-export-only modules (DoD #13) | 0 | 0 |
| Import cycles | 0 (458 edges) | 0 (465 edges) |
| Modules under `src/` | 107 | 109 |
| `domain.py` | 1608 | 1516 |

**DoD #3 rose, and it is the only number that did.** `features/planning/telegram.py` is 616 lines,
up from 584, because the Check review screen has to render Values. Phase 5 owns that file and is
where it should be split; splitting it here for a line count would have been the wrong reason.

**The five new rules were written after the code, not before it.** The packet said they would fail
first; they did not. Each was then verified by reverting its production line and confirming the test
fails — all five do — and `test_writing_a_name_by_hand_does_not_wipe_the_description_it_comes_back_with`
exists only because that check found Q3 had no test at the door it fixes.

**Snapshots moved on exactly these four, and on nothing else:**

| Hash | Why |
|---|---|
| `schema.json` → `check_values` | the new table. No existing table may move: the four models that change file must keep every column type they have |
| `prompt_prefix.json` → `tool:check` | the tool gains two modes and three fields |
| `prompt_prefix.json` → `BOARD_PROMPT` | the `ai_checks` column list is prose there |
| `prompt_prefix.json` → `SYSTEM_PROMPT` | the same column list is prose there too |

`SYSTEM_PROMPT` moving is normally a red flag, because it is the Advisor's cache prefix. Here it is
declared, and it is the same cause as in 4.b.2, when `ai_reminders` gained `next_fire_at_local`: a
view's column list is written out in the prompt so the model knows what it can ask for. What must
**not** move is `PERSONA`, `DIARY_PROMPT` or `tool:reminder`, and nothing volatile may end up in
`messages[0]`. Any other hash that moves is a finding, not a regeneration.
