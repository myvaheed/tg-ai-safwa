from __future__ import annotations

from types import SimpleNamespace

import pytest

from safwa.asr import AudioClip, OpenAITranscriber, TranscriptionError, clip_timeout
from safwa.constants import ASR_TIMEOUT_BASE_SECONDS, ASR_TIMEOUT_PER_AUDIO_SECOND


def transcriber_with(reply: str, *, language: str = "") -> tuple[OpenAITranscriber, list[dict]]:
    transcriber = OpenAITranscriber(
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="whisper-test",
        language=language,
    )
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        return reply

    transcriber.client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))
    )
    return transcriber, calls


def clip(duration: float = 20.0) -> AudioClip:
    return AudioClip(
        data=b"OggS", filename="voice.ogg", mime_type="audio/ogg", duration_seconds=duration
    )


async def test_pinned_language_is_sent_and_an_empty_one_is_not() -> None:
    pinned, pinned_calls = transcriber_with("Готово.", language="ru")
    await pinned.transcribe(clip())
    assert pinned_calls[0]["language"] == "ru"

    auto, auto_calls = transcriber_with("Done.")
    await auto.transcribe(clip())
    assert "language" not in auto_calls[0]


async def test_the_request_timeout_follows_the_audio() -> None:
    transcriber, calls = transcriber_with("Done.")
    await transcriber.transcribe(clip(duration=600.0))
    assert calls[0]["timeout"] == clip_timeout(clip(duration=600.0))
    assert calls[0]["timeout"] == ASR_TIMEOUT_BASE_SECONDS + 600.0 * ASR_TIMEOUT_PER_AUDIO_SECOND


async def test_an_empty_transcript_is_an_error_rather_than_an_empty_turn() -> None:
    transcriber, _ = transcriber_with("   ")
    with pytest.raises(TranscriptionError):
        await transcriber.transcribe(clip())
