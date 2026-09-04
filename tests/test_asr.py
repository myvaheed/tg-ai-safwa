from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import tg_agent_shell.asr as asr_module
from safwa.config import Settings
from tg_agent_shell.asr import (
    ASR_PROGRESS_MIN_AUDIO_SECONDS,
    ASR_TIMEOUT_BASE_SECONDS,
    ASR_TIMEOUT_PER_AUDIO_SECOND,
    FASTER_WHISPER_CPU_COMPUTE_TYPE,
    FASTER_WHISPER_MODEL,
    AudioClip,
    FasterWhisperTranscriber,
    OpenAITranscriber,
    TranscriptionError,
    build_transcriber,
    clip_timeout,
)


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


def settings_for(**overrides) -> Settings:
    values = {"telegram_bot_token": "token", "telegram_owner_id": 1}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def transcriber_for(**overrides):
    """The build the composition root does, so what a setting resolves to is still tested."""
    settings = settings_for(**overrides)
    return build_transcriber(
        provider=settings.asr_provider,
        model=settings.resolved_asr_model,
        base_url=settings.resolved_asr_base_url,
        api_key=settings.asr_api_key.get_secret_value(),
        language=settings.asr_language,
        device=settings.asr_device,
        compute_type=settings.asr_compute_type,
        log_timing=settings.asr_log_timing,
    )


def fake_whisper_model(
    *, cuda: bool, segments: tuple = (), attempts: list | None = None, built: list | None = None
):
    """CTranslate2's shape: the device is only really chosen when the model is built."""

    class FakeWhisperModel:
        def __init__(self, name: str, *, device: str, compute_type: str) -> None:
            if attempts is not None:
                attempts.append((device, compute_type))
            if device == "cuda" and not cuda:
                raise RuntimeError("Library cudnn_ops64_9.dll is not found")
            self.name = name
            self.device = device
            self.compute_type = compute_type
            self.calls: list[dict] = []
            if built is not None:
                built.append(self)

        def transcribe(self, audio, **kwargs):
            self.calls.append({"audio": audio.read(), **kwargs})
            return iter(segments), SimpleNamespace(language="ru")

    return FakeWhisperModel


def segment(text: str, end: float) -> SimpleNamespace:
    return SimpleNamespace(text=text, end=end)


def test_a_missing_cuda_runtime_falls_back_to_the_cpu(monkeypatch) -> None:
    attempts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        asr_module, "_whisper_model_class", lambda: fake_whisper_model(cuda=False, attempts=attempts)
    )

    transcriber = FasterWhisperTranscriber(model="small", device="auto")

    assert [device for device, _compute in attempts] == ["cuda", "cpu"]
    assert transcriber.device == "cpu"
    assert transcriber.compute_type == FASTER_WHISPER_CPU_COMPUTE_TYPE


def test_an_explicit_cpu_device_never_touches_cuda(monkeypatch) -> None:
    attempts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        asr_module, "_whisper_model_class", lambda: fake_whisper_model(cuda=True, attempts=attempts)
    )

    transcriber = FasterWhisperTranscriber(model="small", device="cpu", compute_type="int8_float32")

    assert attempts == [("cpu", "int8_float32")]
    assert transcriber.device == "cpu"


async def test_segments_join_into_one_transcript_and_report_progress(monkeypatch) -> None:
    built: list = []
    monkeypatch.setattr(
        asr_module,
        "_whisper_model_class",
        lambda: fake_whisper_model(
            cuda=True,
            segments=(segment(" Hello", 30.0), segment(" there.", 120.0)),
            built=built,
        ),
    )
    transcriber = FasterWhisperTranscriber(model="small", device="cuda", language="ru")
    seen: list[tuple[float, float]] = []

    async def report(done: float, total: float) -> None:
        seen.append((done, total))

    result = await transcriber.transcribe(clip(duration=120.0), progress=report)

    assert result.text == "Hello there."
    assert built[0].calls[0]["language"] == "ru"
    assert built[0].calls[0]["vad_filter"] is True
    # The second segment arrives inside the throttle window, so only the first is shown.
    assert seen == [(30.0, 120.0)]


async def test_a_short_clip_reports_no_progress(monkeypatch) -> None:
    monkeypatch.setattr(
        asr_module,
        "_whisper_model_class",
        lambda: fake_whisper_model(cuda=True, segments=(segment(" Short.", 5.0),)),
    )
    transcriber = FasterWhisperTranscriber(model="small", device="cuda")
    seen: list[tuple[float, float]] = []

    async def report(done: float, total: float) -> None:
        seen.append((done, total))

    result = await transcriber.transcribe(
        clip(duration=ASR_PROGRESS_MIN_AUDIO_SECONDS - 1), progress=report
    )

    assert result.text == "Short."
    assert seen == []


async def test_a_missing_cuda_library_at_decode_time_retries_on_the_cpu(monkeypatch) -> None:
    """CTranslate2 loads cuBLAS on its first kernel, so the model builds and then fails."""
    attempts: list[tuple[str, str]] = []

    class LazyCuda:
        def __init__(self, name: str, *, device: str, compute_type: str) -> None:
            del name
            attempts.append((device, compute_type))
            self.device = device

        def transcribe(self, audio, **kwargs):
            del audio, kwargs
            if self.device == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return iter((segment(" Recovered.", 5.0),)), SimpleNamespace(language="ru")

    monkeypatch.setattr(asr_module, "_whisper_model_class", lambda: LazyCuda)
    transcriber = FasterWhisperTranscriber(model="small", device="cuda")

    result = await transcriber.transcribe(clip())

    assert result.text == "Recovered."
    assert attempts == [("cuda", "float16"), ("cpu", FASTER_WHISPER_CPU_COMPUTE_TYPE)]
    assert transcriber.device == "cpu"


async def test_a_failed_decode_becomes_a_transcription_error(monkeypatch) -> None:
    class Exploding:
        def __init__(self, name: str, *, device: str, compute_type: str) -> None:
            del name, device, compute_type

        def transcribe(self, audio, **kwargs):
            del audio, kwargs
            raise RuntimeError("the audio could not be decoded")

    monkeypatch.setattr(asr_module, "_whisper_model_class", lambda: Exploding)
    transcriber = FasterWhisperTranscriber(model="small", device="cpu")

    with pytest.raises(TranscriptionError, match="could not be decoded"):
        await transcriber.transcribe(clip())


def test_the_offline_engine_needs_no_api_key(monkeypatch) -> None:
    monkeypatch.setattr(asr_module, "_whisper_model_class", lambda: fake_whisper_model(cuda=False))

    transcriber = transcriber_for(asr_provider="faster_whisper", asr_language="ru")

    assert isinstance(transcriber, FasterWhisperTranscriber)
    assert transcriber.model_name == FASTER_WHISPER_MODEL
    assert transcriber.language == "ru"


def test_the_cuda_wheels_are_registered_before_the_engine_imports(monkeypatch, tmp_path) -> None:
    binaries = tmp_path / "cublas" / "bin"
    binaries.mkdir(parents=True)
    registered: list[str] = []
    monkeypatch.setattr(asr_module, "_cuda_runtime_registered", False)
    monkeypatch.setattr(asr_module.sys, "platform", "win32")
    monkeypatch.setattr(asr_module.os, "add_dll_directory", registered.append, raising=False)
    monkeypatch.setattr(
        asr_module.importlib,
        "import_module",
        lambda name: SimpleNamespace(__path__=[str(tmp_path / name.split(".")[-1])]),
    )

    monkeypatch.setenv("PATH", "C:\\already")

    asr_module._register_cuda_runtime()

    assert registered == [str(binaries)]
    # CTranslate2 loads cuBLAS with its first kernel, through the ordinary search order.
    assert asr_module.os.environ["PATH"].startswith(f"{binaries};C:\\already")


def test_the_missing_extra_names_its_install_command(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)

    with pytest.raises(RuntimeError, match="asr-local"):
        transcriber_for(asr_provider="faster_whisper")
