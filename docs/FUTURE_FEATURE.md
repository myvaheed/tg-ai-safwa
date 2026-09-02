# Ideas kept, and questions already settled

Nothing here is a commitment and nothing here has a scenario. Each entry is a real gap noticed
while something else was being decided, written down so the reasoning does not have to happen
twice. An entry leaves this file by becoming an approved scenario package, or by being dropped.

## The Advisor cannot read the conversation by date

Noticed 2026-08-30, reviewing how the Advisor reads history.

Asked what was said on Tuesday, the Advisor answers from the window it always reads — the newest
messages up to the token budget, stopping at the newest Summary. Only the Diary subagent reaches a
named day, through `read_day`, and a question about the chat is not routed there.

A Summary cannot be the index: it is free text with no dates of its own. Making one Summary per day
was considered and left alone, because it changes when a Summary is written, which is
`CO-SUMMARY-001`.

Ruled: nothing changes. `DI-READ-016` keeps the Diary reading a whole named day; whether the
Advisor gets a way to reach one is designed later.

## `MenuButton` says where it is drawn

Noticed 2026-09-01, making the menu one registry.

`MenuButton(label, row)` moved the menu out of the shell and into the features that own the
screens, which is what it was for. But `row` is layout: the same button belongs in different places
in different keyboards, and a screen has no business naming the row it sits in. `label` is
borderline and stands for now — a screen is called the same wherever it is offered.

The tension is that the shell may not hold a list of features either, which is exactly what `row`
removed. Something has to reconcile the two, and neither side is it.
