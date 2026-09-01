"""The Advisor: the one session that writes to the chat.

It declares no `MODULE`. `MODULES` is what a feature plugs into the application, and the
Advisor is not plugged in — it is the root session every routed one hangs off, wired
directly by the composition root. What lives here is what says *Advisor* rather than *a
session*: the prompt, the views it is told it may read, and the wiring of its own turn.
"""

from __future__ import annotations
