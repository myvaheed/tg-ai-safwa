"""Speech recognition: one voice message in, one transcript out.

Every provider here speaks the same OpenAI-compatible `/audio/transcriptions` API, so a
self-hosted whisper server and a metered endpoint differ only by base URL.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from openai import AsyncOpenAI, OpenAIError

from .config import Settings
from .constants import (
    ASR_MAX_RETRIES,
    ASR_TIMEOUT_BASE_SECONDS,
    ASR_TIMEOUT_PER_AUDIO_SECOND,
)
from .enums import ASRProvider

logger = logging.getLogger(__name__)


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


class Transcriber(Protocol):
    async def transcribe(self, clip: AudioClip) -> TranscriptionResult: ...

    async def close(self) -> None: ...


def clip_timeout(clip: AudioClip) -> float:
    """One request covers the upload and the whole file's decode, so it follows the audio."""
    return ASR_TIMEOUT_BASE_SECONDS + clip.duration_seconds * ASR_TIMEOUT_PER_AUDIO_SECOND


class OpenAITranscriber:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        language: str = "",
        log_timing: bool = True,
    ) -> None:
        self.model = model
        self.language = language
        self.log_timing = log_timing
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "none",
            max_retries=ASR_MAX_RETRIES,
        )

    async def transcribe(self, clip: AudioClip) -> TranscriptionResult:
        started = time.monotonic()
        try:
            response = await self.client.audio.transcriptions.create(
                model=self.model,
                file=(clip.filename, clip.data, clip.mime_type),
                # `text` keeps the response a plain string, so no model has to support
                # the richer formats just to be usable here.
                response_format="text",
                timeout=clip_timeout(clip),
                **({"language": self.language} if self.language else {}),
            )
        except OpenAIError as error:
            raise TranscriptionError(str(error)) from error
        elapsed = time.monotonic() - started
        text = (response if isinstance(response, str) else getattr(response, "text", "")).strip()
        if self.log_timing:
            logger.info(
                "Transcribed %.1fs of audio in %.1fs (RTF %.2f) with %s",
                clip.duration_seconds,
                elapsed,
                elapsed / clip.duration_seconds if clip.duration_seconds else 0.0,
                self.model,
            )
        if not text:
            raise TranscriptionError("The transcription came back empty.")
        return TranscriptionResult(text=text, elapsed_seconds=elapsed)

    async def close(self) -> None:
        await self.client.close()


def build_transcriber(settings: Settings) -> Transcriber | None:
    """The configured transcriber, or None when voice input is off."""
    if not settings.asr_enabled:
        return None
    if (
        settings.asr_provider is not ASRProvider.LOCAL
        and not settings.asr_api_key.get_secret_value()
    ):
        raise RuntimeError(
            f"SAFWA_ASR_PROVIDER={settings.asr_provider.value} needs SAFWA_ASR_API_KEY"
        )
    return OpenAITranscriber(
        base_url=settings.resolved_asr_base_url,
        api_key=settings.asr_api_key.get_secret_value(),
        model=settings.resolved_asr_model,
        language=settings.asr_language,
        log_timing=settings.asr_log_timing,
    )
