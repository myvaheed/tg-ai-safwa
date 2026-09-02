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
