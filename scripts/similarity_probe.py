"""How alike the similar items model finds the owner's own words, to choose SIMILAR_THRESHOLD.

    uv run python scripts/similarity_probe.py
    uv run python scripts/similarity_probe.py "Позвонить маме" "Набрать маму"

With no arguments it prints, for each entity a creating screen compares, the closest pairs of
its open items in `data/safwa.db`, then a fixed set of pairs that should be listed and pairs
that should not; with two texts, the score of that one pair. The first run downloads the model
into `data/models/`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy

from safwa.bootstrap.modules import REGISTRY
from safwa.config import Settings
from tg_agent_shell.foundation.database import Database
from tg_agent_shell.similarity import SIMILAR_MODEL, SIMILAR_THRESHOLD, FastEmbedEncoder

PAIRS_SHOWN = 10

# The same thing said twice: each should be listed.
SAME = (
    ("Позвонить маме", "Набрать маму"),
    ("Купить молоко", "Купить молока в магазине"),
    ("Сходить в спортзал", "Тренировка в зале"),
    ("Разобрать почту", "Разобрать входящие письма"),
    ("Здоровье", "Здоровый образ жизни"),
    ("Write the quarterly report", "Finish the Q3 report"),
    ("Call mom", "Позвонить маме"),
)
# Near in words, different in what is done: none should be listed.
DIFFERENT = (
    ("Позвонить маме", "Позвонить папе"),
    ("Купить молоко", "Купить хлеб"),
    ("Сходить в спортзал", "Сходить к врачу"),
    ("Разобрать почту", "Разобрать шкаф"),
    ("Read a book", "Write a book"),
    ("Позвонить маме", "Починить велосипед"),
)


def _unit(vectors: list) -> numpy.ndarray:
    matrix = numpy.array(vectors, dtype=float)
    return matrix / numpy.linalg.norm(matrix, axis=1, keepdims=True)


def _score(encoder: FastEmbedEncoder, left: str, right: str) -> float:
    a, b = _unit(encoder.encode([left, right]))
    return float(a @ b)


def _print_pair(score: float, left: str, right: str) -> None:
    mark = "listed" if score >= SIMILAR_THRESHOLD else "      "
    print(f"  {score:.3f} {mark}  {left!r} ~ {right!r}")


async def _open_items() -> dict[str, list[tuple[int, str]]]:
    database = Database(Settings().async_database_url)
    try:
        async with database.sessions() as session:
            return {
                entity: list(await similar.open_items(session))
                for entity, similar in REGISTRY.proposals.similar.items()
            }
    finally:
        await database.dispose()


def main() -> None:
    encoder = FastEmbedEncoder(SIMILAR_MODEL, Settings().data_dir / "models")
    print(f"{SIMILAR_MODEL}, SIMILAR_THRESHOLD = {SIMILAR_THRESHOLD}")
    if len(sys.argv) == 3:
        _print_pair(_score(encoder, sys.argv[1], sys.argv[2]), sys.argv[1], sys.argv[2])
        return
    if Path(Settings().database_url.removeprefix("sqlite:///")).exists():
        for entity, items in asyncio.run(_open_items()).items():
            print(f"\n{entity}: {len(items)} open")
            if len(items) < 2:
                continue
            words = [text for _, text in items]
            scores = _unit(encoder.encode(words))
            scores = scores @ scores.T
            pairs = sorted(
                ((scores[i, j], i, j) for i in range(len(words)) for j in range(i + 1, len(words))),
                reverse=True,
            )
            for score, i, j in pairs[:PAIRS_SHOWN]:
                _print_pair(float(score), words[i], words[j])
    for title, pairs in (("\nshould be listed:", SAME), ("\nshould not:", DIFFERENT)):
        print(title)
        for left, right in pairs:
            _print_pair(_score(encoder, left, right), left, right)


if __name__ == "__main__":
    main()
