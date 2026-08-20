# Memory and history usage

## Telegram history

- The private Telegram chat is the source of truth. Safwa rereads real messages through Telethon for every advisor request; SQLite stores only message IDs and semantic classifications.
- The history window is a token budget, not a message count. Safwa reads backwards and stops at the first of roughly 8,000 tokens of messages, the nearest `📜 Summary`, or the oldest message Safwa ever recorded. The cut always lands between messages, never inside one.
- With a Summary in range, the Summary is sent first, then up to 20 older canonical messages for local context, then the newer dialogue.
- Real Safwa dialogue replies use the `assistant` role. Consecutive human messages and other user-side context are combined into one `user` turn with tags such as `[User]` and `[Summary]`. A local timestamp is prepended once per hour of conversation rather than to every message.
- Commands, callbacks, menus, dashboards, forms, approvals, receipts, unsaved item editors, SQL/tool traces, errors, and retrospective PNGs are excluded. Every slash command is deleted from Telegram immediately, which is what makes surviving owner text dialogue.
- The current owner message is correlated across the Bot API and Telethon ID spaces and included exactly once. Only owner text, generated Safwa dialogue, persona reminders, and summaries can enter history.
- Every bot message carries both its `MessageKind` and an immutable Safwa event UUID in invisible characters appended to the Telegram text. SQLite stores the same UUID, so outgoing events are classified by direct lookup even after the visible text is edited. SQLite remains a rebuildable index because the marker is authoritative. Owner messages cannot carry a mark, so their source-message de-duplication still uses the narrow Bot API/Telethon correlation; commands and typed field values are deleted from Telegram.
- Item links are read back as the Markdown the model wrote them in, so a reply citing a Check reaches Safwa's own history as `[Milk](check:14)` rather than as the bare word.
- An unmarked bot message is excluded. Version 1 has no legacy marker fallback.

## Telegram UI lifecycle

- Quick selections, booleans, and navigation update the current UI message instead of creating another one.
- A text-field action replaces the current UI with a focused prompt and a Back button. After input, Safwa deletes the typed field value and restores the same item screen with the new value.
- Card, Tag, and Value creation and viewing/editing follow the same field-oriented convention: one screen listing every valid field, one button per field, edits applied in place. Tag and Value share one renderer; Cards have their own because of the kind-dependent fields and paged selectors. Manual screens end with Back; AI proposal screens end with Save and Discard, and proposed edits show old-to-new differences.
- A validated mutation tool first persists an ordinary proposal. Creation is not in the registry, so a new Card, Check, Tag or Value always reaches the review screen. For an operation that is enabled there, a request-only mini-session checks that every change inside this proposal is a correct part of the final owner request and that it contains nothing extra or erroneous; the proposal is never expected to complete a multi-step request by itself. A passing proposal is saved through the ordinary `ProposalService`, while doubt, failure, an unlisted operation, or disallowed fields opens the unchanged review. AI proposal screens show the complete Card, Tag, Value, or Check overview and human-readable old-to-new differences with exactly Save and Discard; no field controls are exposed, on any screen. Multi-field proposals remain atomic. Autoapproval processes the queue head in order and has no special block for multi-proposal requests, conversational references, or synthetic request sources.
- When the answer is the user's to give, Safwa offers the item instead of proposing one: the reply cites it as `[Milk](check:14)`, which is rendered as a link that opens that Card, Check, Tag, Value, or Request exactly as manual navigation would, buttons included. The reply and its links are ordinary assistant dialogue, so they stay in the chat and in Safwa's own history; only the opened screen is transient UI, and it arrives as a new message rather than replacing the reply. A citation whose item is gone or archived keeps its words and loses its link, and a link that outlives its item answers `⚠️ Error while opening: …`. A Check answer is proposed only when the user already stated it — Passed through the Card verb `complete`, Missed through `cancel`.
- Creating a Check, renaming it, and linking or unlinking it from a Card are AI proposals only; the manual Check screens carry no such buttons. A Card shows its Checks button only when at least one Check already hangs on it.
- Tags and Values can be archived manually from their item screens or through an AI proposal. Archiving is confirmed, removes every direct Card link atomically, and disables Value focus; it does not delete Cards or historical Card events.
- Only one interaction screen is ever live. New ordinary dialogue, any slash command, and any menu navigation remove every other menu and discard an unanswered AI proposal, replacing it with a static assistant message that records what was refused. This holds in both directions: pressing a button on an older dashboard still answers a newer proposal above it.
- Save, Discard, and that frozen screen all read the same way — a heading, the one-line description of the change, and its non-empty fields up to a fixed cap. Both receipts are ordinary assistant dialogue, so Safwa rereads what it saved or was refused.
- Every mutation tool call becomes its own item-style proposal screen; dependent calls are not combined into one approval. Safwa keeps their original order and replaces the same Telegram message with each next Save/Discard screen.
- Save, Discard, or a save failure resolves only the current proposal and advances the queue. After the last item, the same message shows a consolidated Saved/Discarded/Failed result list; failures such as a discarded Tag needed by a later Card link are explicit.
- That owner-facing list is one short line per change, phrased as the operation itself (`✅ Saved — Link Tag “Health” to Goal “Be healthy”`), with references shown by name instead of numeric IDs and no field dump. The model's own tool results keep the full resolved fields and IDs, because it needs them not to repeat its work.
- Read tools run immediately, but the AI resumes only once every proposal is resolved. That continuation receives every mutation result and read-tool result.
- A provider response must not mix `query_safwa` and mutation tools. If it does, reads still run but
  mutations return a short retryable error and must be repeated in the next response after the read
  results are known.
- Mutation calls are prepared independently against committed data. Valid siblings still enter the proposal queue when another call has an unresolved reference. After the queue, structured errors return to the model together; it retries only unfinished calls using IDs from saved results, with at most five repair rounds and no symbolic reference format.
- During one unresolved request, summaries of earlier proposal batches are injected into the next tool-call assistant message as temporary current-request progress. This prevents repeated completed operations, but the summaries are never added to canonical Telegram history, summaries, or memory.
- Manual Card creation exists only in the current transient editor and is inserted on Save; leaving or discarding it creates no persistent record. AI Card creation is an ordinary queued proposal and is inserted only on Save. A discarded AI Card proposal records all proposed fields in its static result message; Goal and Idea screens never expose Action-only fields.
- UI prompts, selections, SQL, and internal tool-result payloads are not persona dialogue. The final generated outcome after the approval queue is resolved becomes the assistant history record.

## Prompt caching

- An advisor request is assembled in four positions, ordered by how often each one changes: the
  static system prompt, then current planning state plus `memory.md`, then the canonical dialogue,
  then a trailing block holding the current local time.
- Only `messages[0]` is a system message. Every later context block travels as a user message
  prefixed `[System]: ` — the Qwen3.5 chat template rejects a second system message.
- The clock is deliberately last. **Any new volatile context belongs after the dialogue, never
  inside a system block.** A value that changes every request invalidates the whole cached prefix
  ahead of it and, on OpenRouter, also scatters sticky provider routing, which identifies a
  conversation by hashing the first system message.
- With cache breakpoints enabled, exactly three `cache_control` markers are placed: after the system
  prompt, after the planning state and memory block, and after the last dialogue message. OpenRouter
  translates them to OpenAI's `prompt_cache_breakpoint` for GPT-5.6 and newer, and automatic caching
  stays enabled alongside them.
- Within one request the message array is append-only, so every tool-call round reuses the previous
  round's prefix. Resuming after an approval queue rebuilds the array from the same four positions,
  so it lands on the same cached prefix rather than a cold one.
- Cache reads and writes are reported per response in the `AI RESPONSE` log line. They are not
  stored in the database.

## Summary

- After an ordinary advisor exchange, Safwa checks the canonical unsummarized dialogue size.
- At approximately 6,000 tokens it creates a visible `📜 Summary` of personal reflections, decisions, reasons, advice, and unresolved topics—not current planning database state or internal operations. `/summarize` writes one immediately, which is how context is cut deliberately.
- The Summary is a general part plus one dated section per day, and the newest day is the detailed one. A new Summary rewrites the previous one instead of replacing it blindly, folding older days into the general part, because only the newest Summary stays in the window.
- The nearest Summary becomes the far edge of the window. If generation fails, the existing Telegram history remains authoritative and Safwa retries later.

## `memory.md`

- `data/memory.md` is authoritative persistent persona memory: UTF-8, one non-empty durable fact per line, approximately 4,000 tokens maximum.
- `memory.md` is injected into advisor prompts after profile settings and active Values. Explicit profile settings and Values take precedence over inferred memory.
- Local edits are imported at startup, before memory-backed prompts, before AI memory maintenance, and by the five-second file-hash watcher.
- AI writes use an atomic replacement and a final hash check, so a newer local edit is not overwritten. SQLite contains only a disposable fact mirror and synchronization metadata.
- A missing file intentionally clears memory. An invalid or oversized file is left untouched, disables memory injection/writes, and produces a deduplicated warning.

## Memory commands

- `/mem <fact>` adds one explicit durable fact directly to `memory.md`.
- `/memory` displays the current validated memory and estimated token usage.
- Removing or editing a fact is done directly in `data/memory.md`; there is no chat command that deletes memory.
- `/syncmem` processes canonical Telegram dialogue back to memory's own cursor, which is the time of the last message it successfully processed. Safwa retells roughly 2K-token chunks, reconciles durable facts, atomically updates `memory.md`, and only then advances the cursor.

## Scheduled memory synchronization

- The Memory sync time in `/settings` enables one automatic `/syncmem`-equivalent run per local calendar day after that time. Setting it to `off` disables it; the default is off.
- The configured value and workspace timezone appear in `/settings`.
- A lightweight scheduler checks eligibility once per minute. It does not call the LLM on startup or every hour: it runs only when the configured time is due and no successful run was recorded that day.
- Foreground advisor generation has priority. If Safwa is busy, the scheduled sync waits for a later check.
- The five-second `memory.md` watcher is separate from scheduled AI synchronization; it only notices and imports local file edits.
