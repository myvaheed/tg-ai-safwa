"""Where a person reaches an agent: the shell around `agent_runtime` and `telegram_llm`.

`ai/` is the engine, `proposals/` is how a change it proposes reaches the owner, and
`session.py` is the two composed into one chain that runs until it answers in words.
`telegram/` is the aiogram surface, `turn/` the single foreground lease, and `cues/` what
the bot says without being asked. The two boundaries outside the chat are one module
each: `asr.py` hears a voice message and `history.py` reads the chat back.

It knows nothing about what the bot is for. Which features exist, what may be read and
what a proposal changes are the application's, declared through `telegram/manifest.py`.
"""

from __future__ import annotations
