"""How alike in meaning short texts are: the similar items a creating review screen lists.

`TextEncoder` is the port — texts in, one vector each out — and `FastEmbedEncoder` its one
adapter: a small multilingual ONNX model that `fastembed` downloads once into the directory
it is given and runs on the CPU. `Similarity` loads it in the background, so nothing waits
on a download, and compares nothing until it has loaded, or once loading failed
(PR-SIMILAR-030).
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# 384 numbers per text, about 0.22 GB, some 50 languages Russian among them.
SIMILAR_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Read off the owner's own titles with scripts/similarity_probe.py. Low enough to list some
# near misses, such as mother and father, rather than miss a duplicate.
SIMILAR_THRESHOLD = 0.70
SIMILAR_ITEMS_SHOWN = 3

Vector = tuple[float, ...]


class TextEncoder(Protocol):
    def encode(self, texts: Sequence[str]) -> list[Sequence[float]]:
        """One vector per text, in order. It blocks, so it is called off the event loop."""


class FastEmbedEncoder:
    def __init__(self, model: str, cache_dir: Path) -> None:
        # Imported here: an application that never compares never loads the runtime.
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model, cache_dir=str(cache_dir))

    def encode(self, texts: Sequence[str]) -> list[Sequence[float]]:
        return [vector.tolist() for vector in self._model.embed(list(texts))]


class Similarity:
    """Compares a new item's words with those of the items already there."""

    def __init__(self, load: Callable[[], TextEncoder]) -> None:
        self._load = load
        self._encoder: TextEncoder | None = None
        # Every text ever compared, as its unit vector. The texts are the owner's titles,
        # so this grows with the workspace and not with time.
        self._vectors: dict[str, Vector] = {}
        # The model runs one call at a time.
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Load the encoder off the event loop. A failure is logged, and nothing is compared."""
        try:
            self._encoder = await asyncio.to_thread(self._load)
        except Exception:
            logger.exception("The similar items model did not load; screens go without the list")

    async def closest(self, words: str, items: Sequence[tuple[int, str]]) -> list[int]:
        """The ids of `items` at least SIMILAR_THRESHOLD alike to `words`, closest first."""
        if self._encoder is None or not words.strip() or not items:
            return []
        try:
            await self._encode(self._encoder, [words, *(text for _, text in items)])
        except Exception:
            logger.exception("The similar items model failed; this screen goes without the list")
            return []
        new = self._vectors[words]
        scored = [(_dot(new, self._vectors[text]), item_id) for item_id, text in items]
        scored.sort(key=lambda pair: -pair[0])
        return [item_id for score, item_id in scored if score >= SIMILAR_THRESHOLD][
            :SIMILAR_ITEMS_SHOWN
        ]

    async def _encode(self, encoder: TextEncoder, texts: Sequence[str]) -> None:
        async with self._lock:
            missing = [text for text in dict.fromkeys(texts) if text not in self._vectors]
            if not missing:
                return
            vectors = await asyncio.to_thread(encoder.encode, missing)
            for text, vector in zip(missing, vectors, strict=True):
                self._vectors[text] = _unit(vector)


def _unit(vector: Sequence[float]) -> Vector:
    length = math.sqrt(sum(value * value for value in vector)) or 1.0
    return tuple(value / length for value in vector)


def _dot(left: Vector, right: Vector) -> float:
    return math.fsum(a * b for a, b in zip(left, right, strict=True))
