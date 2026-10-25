"""`HorizonPiiFinder`: Horizon-Labs/pii-redactor-small as the `PiiFinder`.

The model tags each token with one of 29 kinds of identifier, in 37 languages, Russian among
them; it is an ONNX file that `huggingface_hub` downloads once into the directory it is given.
It runs on the computer's discrete graphics card through DirectML when there is one, and on
the processor otherwise. What it finds and misses, and how fast, is docs/SECURITY.md.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .pseudonymize import Span

REPOSITORY = "Horizon-Labs/pii-redactor-small"
# A commit, so a later upload changes nothing until this line does.
REVISION = "b9d834a12fc946a53551be95ba20ed9c11e9a553"
MODEL_FILE = "onnx/model_quantized.onnx"
# The model was trained on 512 tokens; a longer text is read in windows that overlap by 128.
WINDOW = 512
OVERLAP = 128
# A DATE that close after one of the application's birth words is a date of birth.
BIRTH_CONTEXT_CHARS = 40
LEADING = "«(\"'"
TRAILING = ".,;:!?»)\"'"


class HorizonPiiFinder:
    def __init__(
        self,
        session: Any,
        tokenizer: Any,
        labels: Sequence[str],
        pad: int,
        *,
        fixed_width: bool,
        device: str,
        kept: frozenset[str],
        birth_words: re.Pattern[str],
    ) -> None:
        self._session = session
        self._tokenizer = tokenizer
        self._labels = labels
        self._pad = pad
        self._fixed_width = fixed_width
        self.device = device
        self._kept = kept
        self._birth_words = birth_words
        with_special = tokenizer.encode("a").ids
        bare = tokenizer.encode("a", add_special_tokens=False).ids
        first = with_special.index(bare[0])
        self._prefix, self._suffix = with_special[:first], with_special[first + len(bare) :]
        # DirectML runs one call on a session at a time.
        self._lock = threading.Lock()

    @classmethod
    def load(
        cls, cache_dir: Path, *, kept: frozenset[str], birth_words: re.Pattern[str]
    ) -> HorizonPiiFinder:
        """Download the model on the first call, then open it on the best device there is.

        `kept` names the kinds the application leaves in a request; `birth_words` turns a
        DATE after them into a DATE_OF_BIRTH.
        """
        # Imported here: an application that never pseudonymizes never loads the runtime.
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        def fetch(name: str) -> Path:
            return Path(hf_hub_download(REPOSITORY, name, revision=REVISION, cache_dir=cache_dir))

        config = json.loads(fetch("config.json").read_text(encoding="utf-8"))
        tokenizer = Tokenizer.from_file(str(fetch("tokenizer.json")))
        tokenizer.no_padding()
        tokenizer.no_truncation()
        options = onnxruntime.SessionOptions()
        providers: list[Any] = ["CPUExecutionProvider"]
        card = discrete_gpu(onnxruntime.get_ep_devices())
        if card is not None:
            adapter, device = card
            # One fixed shape lets DirectML compile the whole graph. Without it each of some
            # 1,300 operators is launched on its own, and a short text costs more than on the
            # processor.
            options.add_free_dimension_override_by_name("batch", 1)
            options.add_free_dimension_override_by_name("seq", WINDOW)
            options.enable_mem_pattern = False
            options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
            providers.insert(0, ("DmlExecutionProvider", {"device_id": adapter}))
        session = onnxruntime.InferenceSession(str(fetch(MODEL_FILE)), options, providers=providers)
        labels = [config["id2label"][str(i)] for i in range(len(config["id2label"]))]
        return cls(
            session,
            tokenizer,
            labels,
            config.get("pad_token_id") or 0,
            fixed_width=card is not None,
            device=f"{device} (DirectML)" if card is not None else "CPU",
            kept=kept,
            birth_words=birth_words,
        )

    def find(self, text: str) -> list[Span]:
        encoding = self._tokenizer.encode(text, add_special_tokens=False)
        if not encoding.ids:
            return []
        tags = self._logits(encoding.ids).argmax(-1)
        return decode(
            text,
            encoding.offsets,
            [self._labels[tag] for tag in tags],
            kept=self._kept,
            birth_words=self._birth_words,
        )

    def _logits(self, ids: list[int]) -> np.ndarray:
        size = WINDOW - len(self._prefix) - len(self._suffix)
        total = np.zeros((len(ids), len(self._labels)), dtype=np.float32)
        seen = np.zeros((len(ids), 1), dtype=np.float32)
        for start in range(0, max(len(ids) - OVERLAP, 1), size - OVERLAP):
            row = [*self._prefix, *ids[start : start + size], *self._suffix]
            width = WINDOW if self._fixed_width else len(row)
            input_ids = np.full((1, width), self._pad, dtype=np.int64)
            mask = np.zeros((1, width), dtype=np.int64)
            input_ids[0, : len(row)] = row
            mask[0, : len(row)] = 1
            with self._lock:
                logits = self._session.run(
                    None, {"input_ids": input_ids, "attention_mask": mask}
                )[0][0]
            n = min(size, len(ids) - start)
            total[start : start + n] += logits[len(self._prefix) : len(self._prefix) + n]
            seen[start : start + n] += 1
        return total / seen


def discrete_gpu(devices: Sequence[Any]) -> tuple[int, str] | None:
    """The DirectML adapter number and name of the discrete graphics card the system ranks
    first for high performance; None without one.

    A built-in card is never taken: the one measured read slower than the processor.
    """
    cards = [
        device.device.metadata
        for device in devices
        if device.ep_name == "DmlExecutionProvider"
        and device.device.metadata.get("Discrete") == "1"
    ]
    if not cards:
        return None
    best = min(cards, key=lambda card: int(card["DxgiHighPerformanceIndex"]))
    return int(best["DxgiAdapterNumber"]), best["Description"]


def decode(
    text: str,
    offsets: Sequence[tuple[int, int]],
    labels: Sequence[str],
    *,
    kept: frozenset[str],
    birth_words: re.Pattern[str],
) -> list[Span]:
    """The spans one label per token marks, each grown to its whole words.

    A part of a word left visible is still the word: "@annka_m" tagged from "annka" is
    hidden whole.
    """
    found: list[list[Any]] = []
    for (start, end), label in zip(offsets, labels, strict=True):
        if label == "O":
            continue
        kind = label[2:]
        if found and found[-1][2] == kind and (
            label.startswith("I-") or not text[found[-1][1] : start].strip()
        ):
            found[-1][1] = end
        else:
            found.append([start, end, kind])
    spans = []
    for start, end, kind in found:
        while start < end and text[start].isspace():
            start += 1
        while start > 0 and not text[start - 1].isspace() and text[start - 1] not in LEADING:
            start -= 1
        while end < len(text) and not text[end].isspace():
            end += 1
        while end > start and text[end - 1] in TRAILING:
            end -= 1
        if kind == "DATE" and birth_words.search(text[max(0, start - BIRTH_CONTEXT_CHARS) : start]):
            kind = "DATE_OF_BIRTH"
        if kind not in kept:
            spans.append(Span(start, end, kind))
    return spans
