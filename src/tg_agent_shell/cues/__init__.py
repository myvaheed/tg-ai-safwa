"""Cues: what the Advisor is given to say when nobody asked it anything.

Only the Advisor writes to the chat, so anything the system wants said reaches the owner
as one ordinary Advisor turn. A Cue is the finished request for that turn — the producer
had the facts and wrote them down, so the Advisor relays rather than goes looking.
"""

from __future__ import annotations
