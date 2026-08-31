# Ideas kept for after the migration

Nothing here is a commitment and nothing here has a scenario. Each entry is a real gap noticed
while something else was being decided, written down so the reasoning does not have to happen
twice. An entry leaves this file by becoming an approved scenario package, or by being dropped.

## The Advisor cannot read the conversation by date

Noticed 2026-08-30, while the Phase 8 history packet was being reviewed.

**What is missing.** Ask the Advisor "what did I say about this on Tuesday" and it answers from the
window it always reads: the newest messages up to the token budget, stopping at the newest Summary.
It has no way to reach a named day. The only reader that can is the Diary subagent, through
`read_day`, whose argument is a calendar date — and the Advisor does not route a question about the
conversation to the Diary, because the Diary owns written days, not the chat.

**Why the obvious fix is not obvious.** A Summary is free text a model wrote. It carries no dates
of its own, and the days inside it cannot be told apart, so it cannot serve as an index into the
conversation. Making one Summary per day, or making a Summary carry its days as structure, was
considered on 2026-08-30 and left alone: it changes when a Summary is written, which is
`CO-SUMMARY-001`, and the window it belongs to.

**What was ruled instead.** Nothing changes for now. `DI-READ-016` records that the Diary reads a
whole named day whatever a Summary in the middle of it says; that stays a Diary rule. Whether the
Advisor gets a way to reach a day, and what shape it takes, is a feature to design after the
migration.
