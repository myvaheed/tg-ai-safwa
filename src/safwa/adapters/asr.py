"""Speech recognition: one voice message in, one transcript out.

Every hosted provider here speaks the same OpenAI-compatible `/audio/transcriptions` API, so a
self-hosted whisper server and a metered endpoint differ only by base URL. `faster_whisper`
is the one engine that decodes in this process instead, behind the same interface.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from llm_gateway import OpenAICompatibleError, create_openai_client
from telegram_llm import (
    AudioClip,
    ProgressCallback,
    Transcriber,
    TranscriptionError,
    TranscriptionResult,
)

from ..config import Settings
from ..enums import ASRProvider

logger = logging.getLogger(__name__)

# One call covers the upload and the whole file's decode, so the budget follows the audio.
ASR_TIMEOUT_BASE_SECONDS = 60.0
ASR_TIMEOUT_PER_AUDIO_SECOND = 1.0
# An upload is expensive to repeat, so a failure is retried less eagerly than a chat call.
ASR_MAX_RETRIES = 2
# CTranslate2 has no float16 kernel on the CPU, so each device carries its own default.
FASTER_WHISPER_CPU_COMPUTE_TYPE = "int8"
FASTER_WHISPER_CUDA_COMPUTE_TYPE = "float16"
# A CPU fallback drops these rather than let CTranslate2 silently widen them to float32.
FASTER_WHISPER_CUDA_ONLY_COMPUTE_TYPES = frozenset({"float16", "int8_float16"})
# The `nvidia-*-cu12` wheels of the asr-cuda extra, whose DLLs CTranslate2 loads by name.
CUDA_RUNTIME_PACKAGES = ("cublas", "cudnn", "cuda_nvrtc")
FASTER_WHISPER_BEAM_SIZE = 5
# A local decode is silent for minutes, so it reports a percentage. Shorter audio
# finishes before the first edit would land, and Telegram rate-limits edits.
ASR_PROGRESS_MIN_AUDIO_SECONDS = 60.0
ASR_PROGRESS_MIN_INTERVAL_SECONDS = 5.0


def clip_timeout(clip: AudioClip) -> float:
    """One request covers the upload and the whole file's decode, so it follows the audio."""
    return ASR_TIMEOUT_BASE_SECONDS + clip.duration_seconds * ASR_TIMEOUT_PER_AUDIO_SECOND


def _log_elapsed(clip: AudioClip, elapsed: float, model: str) -> None:
    logger.info(
        "Transcribed %.1fs of audio in %.1fs (RTF %.2f) with %s",
        clip.duration_seconds,
        elapsed,
        elapsed / clip.duration_seconds if clip.duration_seconds else 0.0,
        model,
    )


class _ProgressReporter:
    """Throttled percentage for a long decode; a short clip never gets one."""

    def __init__(self, clip: AudioClip, callback: ProgressCallback | None) -> None:
        self.total = clip.duration_seconds
        self.callback = callback if self.total >= ASR_PROGRESS_MIN_AUDIO_SECONDS else None
        self.last = 0.0

    async def reached(self, done: float) -> None:
        if self.callback is None:
            return
        now = time.monotonic()
        if self.last and now - self.last < ASR_PROGRESS_MIN_INTERVAL_SECONDS:
            return
        self.last = now
        try:
            await self.callback(min(done, self.total), self.total)
        except Exception:
            # A refused edit is not worth losing the transcript over.
            logger.debug("Could not report transcription progress", exc_info=True)
            self.callback = None


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
        self.client = create_openai_client(
            base_url=base_url,
            api_key=api_key or "none",
            max_retries=ASR_MAX_RETRIES,
        )

    async def transcribe(
        self, clip: AudioClip, *, progress: ProgressCallback | None = None
    ) -> TranscriptionResult:
        # The endpoint answers once, with the whole transcript: there is nothing to report.
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
        except OpenAICompatibleError as error:
            raise TranscriptionError(str(error)) from error
        elapsed = time.monotonic() - started
        text = (response if isinstance(response, str) else getattr(response, "text", "")).strip()
        if self.log_timing:
            _log_elapsed(clip, elapsed, self.model)
        if not text:
            raise TranscriptionError("The transcription came back empty.")
        return TranscriptionResult(text=text, elapsed_seconds=elapsed)

    async def close(self) -> None:
        await self.client.close()


_cuda_runtime_registered = False


def _register_cuda_runtime() -> None:
    """Point the loader at the `nvidia-*-cu12` wheels, which CTranslate2 loads by name.

    Those wheels put the DLLs inside `site-packages/nvidia/*/bin`, where nothing looks by
    itself, so `--extra asr-cuda` would otherwise install and never be found. Both steps
    are needed: `add_dll_directory` covers the load-time dependencies, and `PATH` covers
    cuBLAS, which CTranslate2 loads with its first kernel through the ordinary search
    order. On Linux the loader reads `LD_LIBRARY_PATH`, set before startup.
    """
    global _cuda_runtime_registered
    if _cuda_runtime_registered or sys.platform != "win32":
        return
    _cuda_runtime_registered = True
    directories: list[str] = []
    for package in CUDA_RUNTIME_PACKAGES:
        try:
            module = importlib.import_module(f"nvidia.{package}")
        except ImportError:
            continue
        for parent in getattr(module, "__path__", ()):
            directory = Path(parent) / "bin"
            if directory.is_dir():
                directories.append(str(directory))
                # The cookie removes the directory when closed, so it is kept alive here.
                _CUDA_DLL_COOKIES.append(os.add_dll_directory(str(directory)))
    if directories:
        os.environ["PATH"] = os.pathsep.join([*directories, os.environ.get("PATH", "")])
        logger.debug("CUDA runtime found at %s", directories)


def _whisper_model_class() -> Any:
    _register_cuda_runtime()
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "SAFWA_ASR_PROVIDER=faster_whisper needs the offline engine: "
            "uv sync --extra asr-local (or --extra asr-cuda for the GPU)"
        ) from error
    return WhisperModel


# CTranslate2 loads cuBLAS/cuDNN when it runs its first kernel, not when the model is
# built, so a missing DLL can surface a whole decode later. Matched to keep an ordinary
# decode failure from costing the GPU for the rest of the session.
_CUDA_FAILURE_MARKERS = ("cublas", "cudnn", "cuda")
# Held for the process lifetime: closing a cookie unregisters its directory again.
_CUDA_DLL_COOKIES: list[Any] = []


def _open_whisper_model(name: str, device: str, compute_type: str) -> tuple[str, str, Any]:
    """The model on the best device that actually loads.

    On Windows the GPU path fails for one reason — cuBLAS/cuDNN 9 are not on PATH — and
    it fails either here or on the first decode. Falling back keeps the bot up.
    """
    model_class = _whisper_model_class()
    if device != "cpu":
        wanted = compute_type or FASTER_WHISPER_CUDA_COMPUTE_TYPE
        try:
            return "cuda", wanted, model_class(name, device="cuda", compute_type=wanted)
        except Exception as error:
            if device == "cuda":
                logger.warning("SAFWA_ASR_DEVICE=cuda is unavailable: %s", error)
            else:
                logger.info("No usable CUDA runtime, transcribing on the CPU: %s", error)
    resolved = compute_type or FASTER_WHISPER_CPU_COMPUTE_TYPE
    return "cpu", resolved, model_class(name, device="cpu", compute_type=resolved)


class FasterWhisperTranscriber:
    """CTranslate2 in this process: no server, no network, no API key."""

    def __init__(
        self,
        *,
        model: str,
        device: str = "auto",
        compute_type: str = "",
        language: str = "",
        log_timing: bool = True,
    ) -> None:
        self.model_name = model
        self.language = language
        self.log_timing = log_timing
        self.requested_compute_type = compute_type
        # Built once, at startup: loading weights per voice message would cost more than
        # the decode, and the resolved device is logged here rather than measured later.
        self.device, self.compute_type, self.model = _open_whisper_model(
            model, device, compute_type
        )
        logger.info(
            "faster-whisper %s ready on %s (%s)", model, self.device, self.compute_type
        )

    async def transcribe(
        self, clip: AudioClip, *, progress: ProgressCallback | None = None
    ) -> TranscriptionResult:
        try:
            return await self._decode(clip, progress)
        except TranscriptionError as error:
            if not self._rebuild_on_cpu(error):
                raise
        return await self._decode(clip, progress)

    def _rebuild_on_cpu(self, error: TranscriptionError) -> bool:
        """Whether a CUDA failure was traded for the CPU, leaving the recording to retry."""
        if self.device != "cuda":
            return False
        if not any(marker in str(error).lower() for marker in _CUDA_FAILURE_MARKERS):
            return False
        logger.warning("CUDA failed during the decode, rebuilding on the CPU: %s", error)
        wanted = self.requested_compute_type
        if wanted in FASTER_WHISPER_CUDA_ONLY_COMPUTE_TYPES:
            wanted = ""
        self.device, self.compute_type, self.model = _open_whisper_model(
            self.model_name, "cpu", wanted
        )
        return True

    async def _decode(
        self, clip: AudioClip, progress: ProgressCallback | None
    ) -> TranscriptionResult:
        started = time.monotonic()
        loop = asyncio.get_running_loop()
        # Whisper's own long-form loop yields segments as it decodes, so the queue is what
        # turns a silent wait into a percentage.
        ends: asyncio.Queue[float | None] = asyncio.Queue()
        parts: list[str] = []

        def decode() -> None:
            try:
                segments, _info = self.model.transcribe(
                    BytesIO(clip.data),
                    language=self.language or None,
                    # Bundled Silero: it drops the silence Whisper otherwise invents words over.
                    vad_filter=True,
                    beam_size=FASTER_WHISPER_BEAM_SIZE,
                )
                for segment in segments:
                    parts.append(segment.text)
                    loop.call_soon_threadsafe(ends.put_nowait, float(segment.end))
            finally:
                loop.call_soon_threadsafe(ends.put_nowait, None)

        reporter = _ProgressReporter(clip, progress)

        async def report() -> None:
            while True:
                end = await ends.get()
                if end is None:
                    break
                await reporter.reached(end)

        # No timeout: a local decode cannot hang on a network, and abandoning the wait
        # would leave the thread running anyway.  The report ends when the decode does,
        # because the decode posts its own end marker whether it finished or raised.
        try:
            await asyncio.gather(asyncio.to_thread(decode), report())
        except Exception as error:
            raise TranscriptionError(str(error)) from error

        elapsed = time.monotonic() - started
        text = "".join(parts).strip()
        if self.log_timing:
            _log_elapsed(clip, elapsed, f"{self.model_name} on {self.device}")
        if not text:
            raise TranscriptionError("The transcription came back empty.")
        return TranscriptionResult(text=text, elapsed_seconds=elapsed)

    async def close(self) -> None:
        # CTranslate2 releases the model with its last reference; there is nothing to await.
        self.model = None


def build_transcriber(settings: Settings) -> Transcriber | None:
    """The configured transcriber, or None when voice input is off."""
    if not settings.asr_enabled:
        return None
    if settings.asr_provider is ASRProvider.FASTER_WHISPER:
        return FasterWhisperTranscriber(
            model=settings.resolved_asr_model,
            device=settings.asr_device,
            compute_type=settings.asr_compute_type,
            language=settings.asr_language,
            log_timing=settings.asr_log_timing,
        )
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
