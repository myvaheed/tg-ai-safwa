# Memory and history usage

## Telegram history

- The private Telegram chat is the source of truth. Safwa rereads real messages through Telethon for every advisor request; SQLite stores only message IDs and semantic classifications.
- History must have a visible boundary: `/newsession <initial request>` or the nearest `📜 Summary`. Without one, advisor requests and `/syncmem` stop and explain how to start a session.
- With a `/newsession` boundary, its text is sent as `[Initial request]`, followed by canonical dialogue after it.
- With a Summary boundary, the Summary is sent first, then up to 20 older canonical messages with short UTC timestamps for local context, then the newer dialogue.
- Real Safwa dialogue replies use the `assistant` role. Consecutive human messages and other user-side context are combined into one `user` turn with tags such as `[User]`, `[Summary]`, and `[Initial request]`.
- Commands, callbacks, menus, dashboards, forms, approvals, receipts, drafts, SQL/tool traces, errors, and retrospective PNGs are excluded. Only registered user dialogue, generated Safwa dialogue, persona reminders, boundaries, and subsession results can enter history.

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

