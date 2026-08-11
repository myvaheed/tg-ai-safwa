# Memory and history usage

## Telegram history

- The private Telegram chat is the source of truth. Safwa rereads real messages through Telethon for every advisor request; SQLite stores only message IDs and semantic classifications.
- History must have a visible boundary: `/newsession <initial request>` or the nearest `📜 Summary`. Without one, advisor requests and `/syncmem` stop and explain how to start a session.
- With a `/newsession` boundary, its text is sent as `[Initial request]`, followed by canonical dialogue after it.
- With a Summary boundary, the Summary is sent first, then up to 20 older canonical messages with short UTC timestamps for local context, then the newer dialogue.
- Real Safwa dialogue replies use the `assistant` role. Consecutive human messages and other user-side context are combined into one `user` turn with tags such as `[User]`, `[Summary]`, and `[Initial request]`.
- Commands, callbacks, menus, dashboards, forms, approvals, receipts, unsaved item editors, SQL/tool traces, errors, and retrospective PNGs are excluded. Every slash command is deleted from Telegram immediately except `/newsession`, which remains visible as the history boundary.
- The current user message is correlated across the Bot API and Telethon ID spaces and included exactly once. Only registered user dialogue, generated Safwa dialogue, persona reminders, boundaries, and subsession results can enter history.

## Telegram UI lifecycle

- Quick selections, booleans, and navigation update the current UI message instead of creating another one.
- A text-field action replaces the current UI with a focused prompt and a Back button. After input, Safwa deletes the typed field value and restores the same item screen with the new value.
- Card, Tag, and Value creation and viewing/editing follow the same field-oriented convention: one screen listing every valid field, one button per field, edits applied in place. Tag and Value share one renderer; Cards have their own because of the kind-dependent fields and paged selectors. Manual screens end with Back; AI proposal screens end with Save and Discard, and proposed edits show old-to-new differences.
- A validated mutation tool opens a read-only item review, never a generic free-form approval. AI proposal screens show the complete Card, Tag, or Value overview and human-readable old-to-new differences with exactly Save and Discard; no field controls are exposed. Multi-field proposals remain atomic, and the committed item stays unchanged until Save. Manual item editors retain their normal field controls.
- Tags and Values can be archived manually from their item screens or through an AI proposal. Archiving is confirmed, removes every direct Card link atomically, and disables Value focus; it does not delete Cards or historical Card events.
- An interaction UI may only be the latest chat element. New ordinary dialogue removes obsolete menus and automatically discards an unanswered AI proposal, replacing it with a static assistant message that records the complete proposed change.
- Every mutation tool call becomes its own item-style proposal screen; dependent calls are not combined into one approval. Safwa keeps their original order and replaces the same Telegram message with each next Save/Discard screen.
- Save, Discard, or a save failure resolves only the current proposal and advances the queue. After the last item, the same message shows a consolidated Saved/Discarded/Failed result list; failures such as a discarded Tag needed by a later Card link are explicit.
- Read tools run immediately, but the AI resumes only once every proposal is resolved. That continuation receives every mutation result and read-tool result.
- Mutation calls are prepared independently against committed data. Valid siblings still enter the proposal queue when another call has an unresolved reference. After the queue, structured errors return to the model together; it retries only unfinished calls using IDs from saved results, with at most five repair rounds and no symbolic reference format.
- During one unresolved request, summaries of earlier proposal batches are injected into the next tool-call assistant message as temporary current-request progress. This prevents repeated completed operations, but the summaries are never added to canonical Telegram history, summaries, or memory.
- Manual Card creation exists only in the current transient editor and is inserted on Save; leaving or discarding it creates no persistent record. AI Card creation is an ordinary queued proposal and is inserted only on Save. A discarded AI Card proposal records all proposed fields in its static result message; Goal and Idea screens never expose Action-only fields.
- UI prompts, selections, SQL, and internal tool-result payloads are not persona dialogue. The final generated outcome after the approval queue is resolved becomes the assistant history record.

## Sessions and subsessions

- `/newsession <initial request>` creates the current history boundary.
- `/endsession [result instruction]` offers a confirmation to compress everything since the active `/newsession`.
- On approval, Safwa generates one compact subsession result, deletes that branch from Telegram, and leaves the result as canonical user-side context. Cancelling keeps the branch unchanged.

## Summary

- After an ordinary advisor exchange, Safwa checks the canonical unsummarized dialogue size.
- At approximately 10,000 tokens it creates a visible `📜 Summary` of personal reflections, decisions, reasons, advice, and unresolved topics—not current planning database state or internal operations.
- The nearest Summary becomes the next history boundary. If generation fails, the existing Telegram history remains authoritative and Safwa retries later.

## `memory.md`

- `data/memory.md` is authoritative persistent persona memory: UTF-8, one non-empty durable fact per line, approximately 4,000 tokens maximum.
- `memory.md` is injected into advisor prompts after profile settings and active Values. Explicit profile settings and Values take precedence over inferred memory.
- Local edits are imported at startup, before memory-backed prompts, before AI memory maintenance, and by the five-second file-hash watcher.
- AI writes use an atomic replacement and a final hash check, so a newer local edit is not overwritten. SQLite contains only a disposable fact mirror and synchronization metadata.
- A missing file intentionally clears memory. An invalid or oversized file is left untouched, disables memory injection/writes, and produces a deduplicated warning.

## Memory commands

- `/mem <fact>` adds one explicit durable fact directly to `memory.md`.
- `/memory` displays the current validated memory and estimated token usage.
- `/forget <line number>` removes one displayed fact.
- `/syncmem` processes new canonical Telegram dialogue since the last successful memory boundary. Safwa retells roughly 2K-token chunks, reconciles durable facts, atomically updates `memory.md`, and only then advances the processed-message marker.

## Scheduled memory synchronization

- `/setmemtime HH:MM` enables one automatic `/syncmem`-equivalent run per local calendar day after that time. `/setmemtime off` disables it; the default is off.
- The configured value and workspace timezone appear in `/settings`.
- A lightweight scheduler checks eligibility once per minute. It does not call the LLM on startup or every hour: it runs only when the configured time is due and no successful run was recorded that day.
- Foreground advisor generation has priority. If Safwa is busy, the scheduled sync waits for a later check.
- The five-second `memory.md` watcher is separate from scheduled AI synchronization; it only notices and imports local file edits.
