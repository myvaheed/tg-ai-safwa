# Restructuring

**Every point should be short in one line.**

Nothing here is decided and nothing here is scheduled. A candidate is one line: what moves, and
what it costs or breaks. Each is taken apart on its own later. A candidate leaves this file by
becoming a batch, or by being ruled out in place.

Verdicts: **yes** — the move is right as stated. **split** — part of it is right. **first** —
something else has to happen before it can. **no** — ruled out, kept so it is not raised twice.

Twenty-three batches have landed and are not listed here: what each did is the code, and why it was
done that way is the document that owns the mechanism. What a reader can get from neither is kept —
the candidates still open, and the reason a candidate was refused, so it is not raised a second
time. Open work from the polishing pass is [POLISHING.md](POLISHING.md), and each unfinished item
has one of these two as its owner, never both.

## Open

29. **yes** — most of the workspace block is approved nowhere. `WS-JUDGE-002` covers the mutator
    being handed it before its first step, and `PS-CONTEXT-001`, `PL-CRITERIA-004`,
    `PL-CONTEXT-010` and `RM-READ-024` each cover one slice from their own feature's file. What is
    left over: that the root session reads the block too, and not only a subagent; Values, Tags and
    the workspace mode, described nowhere; and the critical-Card rules — ten of them, the ones
    carrying a Value in focus first, Done and Cancelled left out — which are a test with no
    scenario, so any batch may change them without asking. Nothing states either that every item is
    written as a link the model may point at in its own reply, which is what `citation()` exists
    for. Found while answering 19.

## Ruled out

2. **no** — moving the review flow under `ai/`. It travelled with the engine rather than into it,
   and the move would reverse `telegram`, `bootstrap` and `MutationCatalogue`.
19. **no** — splitting the workspace block into per-feature contributions. `state.py` reads six
    models from five packages, but the split buys nothing and costs the order. The block is one
    text a small model reads, stable parts first; contributions in `MODULES` order would put Cards
    before Values and the Sprint after Tags, and pinning the order back means a list somewhere —
    the registry the split was meant to remove. The cross-feature knowledge survives it too:
    critical Cards are ordered by whether they carry an active Value, so that query still reads
    Values from Cards, and Today Actions appear only while a Sprint runs, so an inline `if` becomes
    Cards asking Planning. And the cost the candidate assumes has never been paid: `state.py` has
    five commits, every one a restructuring move, none of them an entity added to the block.
    Revisit if a feature ever needs its own lines in it without touching the others.
24. **no** — splitting the Cards Telegram adapter. Eleven modules, and the biggest is 375 lines
    against a 600 threshold that one module in the repo crosses, none of them here. Inside is a DAG
    with no cycle: `presentation.py` is the leaf five of them read, `handlers.py` the dispatcher
    nothing reads. Outside, three imports reach in, all through the package `__init__` and none
    into a submodule, so there is no boundary to repair. The stage lists are `lists.py`, one
    module: a package of one is not a package. Revisit if a module here passes 600 lines, or if an
    import names a submodule.
25. **no** — merging Values and Tags. They are the same nine modules twice, but each keeps its own
    rules, and `RecordToolInput` is already the whole of what they share.
