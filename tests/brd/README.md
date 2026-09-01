# The scenarios

`tests/brd/<feature>.feature` carries one approved business rule per `Scenario`, written
Given-When-Then under an identifier that outlives any test name. It is the highest authority on
what Safwa does: when code, tests and docs disagree, an approved scenario wins.

No BDD framework. Readable pytest is enough — the `.feature` file is a traceability contract, and
`tests/test_brd_traceability.py` is what keeps it connected to the tests that cite it.

What a scenario has to look like lives here, next to the scenarios themselves.

## Identifier

`<AREA>-<TOPIC>-<NNN>`, zero-padded, never reused and never renumbered inside its own package.

The **topic is the aspect the rule is about**, never the package's own name written twice:
`DI-MOOD-004` and `DI-DELETE-005`, not `DI-DIARY-004`. An identifier that repeats its area says
nothing a reader can use, and a package whose scenarios all share one topic has not been read
carefully enough to say what each of them is about.

One `.feature` file is one package under `src/safwa/features/`, so a rule and the code that keeps
it are found in one place. `screens.feature` is the one exception: it is owned by
`src/safwa/telegram`, which Phase 8 turns into a package of its own. The prefix names that package, never a group of them: `PL` used to cover
Cards, Checks, Values, Tags and the Sprint at once, which meant five packages sharing one numbering
line and no way to read an identifier and know where its code lives.

| Area | Prefix | Area | Prefix |
|---|---|---|---|
| Cards | `CD` | Proposals | `PR` |
| Checks | `CH` | Agents and routing | `AG` |
| Values | `VL` | Telegram history | `TG` |
| Tags | `TA` | Continuity and memory | `CO` |
| Planning — the Sprint and the mode without one | `PL` | Diary | `DI` |
| Reminders | `RM` | Saved Requests | `SR` |
| Profile and Settings | `PS` | The heavy analyzer | `HAN` |
| Screens | `SC` | | |

`tests/test_brd_traceability.py` reads this table, so a prefix that is not in it is not a
scenario identifier.

## Format

```gherkin
  Scenario: CH-GATE-006 — A Card cannot be Done with an unanswered Check
    Given an Action carrying a Check that has never been answered
    When the owner finishes it as Done
    Then it is refused, and the refusal names the Check that is still unanswered
    And nothing about the Action changed
    When the owner cancels the same Action instead
    Then it is cancelled with the Check left unanswered
```

The title after the em dash states the rule, not the mechanism.

The test's docstring is the identifier and the file that carries it, and nothing else. The scenario
text lives in the `.feature` file; restating it in the test would be a second copy to keep in step.

```python
async def test_a_card_cannot_be_done_with_an_unanswered_check(sessions):
    """CH-GATE-006 — tests/brd/checks.feature"""
```

## Language

A `.feature` file is read by someone who has never seen the code. Write it in the owner's words, not
the codebase's.

- Say what happens to the owner, not which function does it. "The Reminder keeps its next fire", not
  "no schedule column changes".
- Name a mechanism only when the rule is *about* that mechanism. DI-READ-013 names its two readers
  because which two the subagent holds **is** the rule; DI-DAY-001 names nothing, because writing
  down a day is not about a function.
- No internal nouns where the owner has a word for it. `provider input`, `cursor`, `hash race`,
  `snapshot`, `workspace revision`, `row` — each has a plain equivalent, and the plain one is right
  unless the rule is about the mechanism.
- Domain nouns stay capitalized and exact: Card, Sprint, Value, Tag, Check, Request, Reminder,
  Summary, Diary.
- **A source is never copied word for word.** Read what it is getting at, check it against the
  code, and write that. Where the two disagree, the disagreement ships as a `question`.

The identifier line is the exception: it is an identifier, and it never changes wording once
approved. The `Scenario:` title after the em dash is prose and may be made clearer.

## Numbers

A scenario states the number and names the constant next to it. The test reads the constant.

```text
Then the 3 oldest go to the Advisor as one request (REMINDER_FIRE_BATCH = 3)
```

Writing `the batch size` in the scenario says nothing a reviewer can check, and hard-coding `3` in
the test makes the test and the constant two copies that can disagree in silence. Written this way
the scenario is readable on its own, the test still follows the constant when it is tuned, and a
constant that moves away from its scenario is visible to whoever reads the two side by side.

## The traceability is checked

`tests/test_brd_traceability.py` reads the `.feature` files and every test docstring, and
fails on an approved scenario with no test, a test citing a scenario that does not exist, a
docstring in any other shape, a prefix missing from the table above, and a repeated scenario
title. Without it a renamed scenario or a deleted test stays green.

A `.feature` file carries **no Gherkin tags**, and the same test fails on one. No BDD runner
reads these files, so a `@di_day_011` above a scenario is a lowercase second copy of the
identifier that nothing keeps in step. The `Scenario:` line is where the identifier lives.

Two Scenario blocks may share an identifier only when they are two observable cases of the
**same** question — a rule's two branches, or one rule at two doors. Two different rules under
one identifier hide the second one, and no test failure will say so.

The identifier is written in the docstring only. `tests/conftest.py` reads it from there and
attaches the marker, so both of these work with nothing to keep in step:

```bash
uv run pytest -m brd -q
```

```bash
uv run pytest --brd=DI-DAY-001 -q
```
