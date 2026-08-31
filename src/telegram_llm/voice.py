"""A voice message, and what has to happen before it can be part of the conversation.

A voice message carries no text, so nothing in the chat says what the person said. Whatever
turns the audio into words is the host's, and it is only ever asked for the words: which
engine, where it runs, and what it costs are none of the chat's business.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AudioClip:
    data: bytes
    # The endpoint reads the container from the extension, so the name has to be real.
    filename: str
    mime_type: str
    duration_seconds: float


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    elapsed_seconds: float = 0.0


class TranscriptionError(RuntimeError):
    """Anything that stopped a voice message from becoming text."""


# Decoded audio seconds and the clip's total. Only an engine that decodes locally calls it.
ProgressCallback = Callable[[float, float], Awaitable[None]]


class Transcriber(Protocol):
    async def transcribe(
        self, clip: AudioClip, *, progress: ProgressCallback | None = None
    ) -> TranscriptionResult: ...

    async def close(self) -> None: ...
