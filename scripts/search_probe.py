"""How close the text model finds the owner's own words, to choose TEXT_MODEL's two cut-offs.

    uv run python scripts/search_probe.py
    uv run python scripts/search_probe.py "Позвонить маме" "Набрать маму"
    uv run python scripts/search_probe.py --search "зубной врач"

With no arguments it prints, for `alike`, the closest pairs of open items per creating screen in
`data/safwa.db` (it asks for the passphrase), then fixed pairs that should and should not be
listed; and, for `related`, fixed searches with texts they should and should not find. With two
texts, the score of that one pair against both cut-offs. With `--search`, the owner's searchable
texts closest in meaning to those words. The first run downloads the model into `data/models/`.
"""

from __future__ import annotations

import asyncio
import sys
from zoneinfo import ZoneInfo

import numpy

from safwa.bootstrap.modules import AI_VIEWS, REGISTRY, TEXT_MODEL
from safwa.config import Settings
from safwa.security import unlock
from tg_agent_shell.ai.sql import add_view_functions
from tg_agent_shell.foundation.database import Database, DatabaseFile
from tg_agent_shell.search.words import FastEmbedEncoder

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
# A search and a text it is about: each should be found.
FOUND = (
    ("зубной врач", "Записаться к стоматологу"),
    ("спорт", "Тренировка в зале"),
    ("деньги за квартиру", "Оплатить аренду"),
    ("поездка на море", "Ездили на пляж, купались весь день"),
    ("mom", "Позвонить маме"),
)
# A search and a text it is not about: none should be found.
MISSED = (
    ("зубной врач", "Купить молоко"),
    ("спорт", "Разобрать почту"),
    ("деньги за квартиру", "Позвонить маме"),
    ("поездка на море", "Весь день работал над отчётом"),
)


def _unit(vectors: list) -> numpy.ndarray:
    matrix = numpy.array(vectors, dtype=float)
    return matrix / numpy.linalg.norm(matrix, axis=1, keepdims=True)


def _score(encoder: FastEmbedEncoder, left: str, right: str) -> float:
    a, b = _unit(encoder.encode([left, right]))
    return float(a @ b)


def _print(score: float, mark: str, left: str, right: str) -> None:
    print(f"  {score:.3f} {mark:7} {left!r} ~ {right!r}")


def _listed(score: float) -> str:
    return "listed" if score >= TEXT_MODEL.alike else ""


def _found(score: float) -> str:
    return "found" if score >= TEXT_MODEL.related else ""


async def _open_items(file: DatabaseFile) -> dict[str, list[tuple[int, str]]]:
    database = Database(file)
    try:
        async with database.sessions() as session:
            return {
                entity: list(await similar.open_items(session))
                for entity, similar in REGISTRY.proposals.similar.items()
            }
    finally:
        await database.dispose()


def _searchable_texts(file: DatabaseFile) -> list[tuple[str, str]]:
    """Every searchable text as the index reads it: (item_type:id, text)."""
    connection = file.connect(read_only=True)
    add_view_functions(connection, ZoneInfo("UTC"))
    texts = []
    try:
        for view in AI_VIEWS:
            if view.searchable is None:
                continue
            columns = ", ".join(f'"{field}"' for field in view.searchable.fields)
            for item_id, *values in connection.execute(f"SELECT id, {columns} FROM {view.name}"):
                label = f"{view.searchable.item_type}:{item_id}"
                texts.extend((label, str(value)) for value in values if value)
    finally:
        connection.close()
    return texts


def main() -> None:
    settings = Settings()
    encoder = FastEmbedEncoder(TEXT_MODEL.name, settings.data_dir / "models")
    print(f"{TEXT_MODEL.name}, alike = {TEXT_MODEL.alike}, related = {TEXT_MODEL.related}")
    if len(sys.argv) == 3 and sys.argv[1] == "--search":
        file = unlock(settings.database_path)
        texts = _searchable_texts(file)
        scores = _unit(encoder.encode([sys.argv[2], *(text for _, text in texts)]))
        closeness = scores[1:] @ scores[0]
        for index in numpy.argsort(-closeness)[:PAIRS_SHOWN]:
            label, text = texts[index]
            _print(float(closeness[index]), _found(float(closeness[index])), label, text[:80])
        return
    if len(sys.argv) == 3:
        score = _score(encoder, sys.argv[1], sys.argv[2])
        _print(score, _listed(score) or _found(score), sys.argv[1], sys.argv[2])
        return
    if settings.database_path.exists():
        file = unlock(settings.database_path)
        for entity, items in asyncio.run(_open_items(file)).items():
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
                _print(float(score), _listed(float(score)), words[i], words[j])
    for title, pairs, mark in (
        ("\nalike, should be listed:", SAME, _listed),
        ("\nalike, should not:", DIFFERENT, _listed),
        ("\nrelated, should be found:", FOUND, _found),
        ("\nrelated, should not:", MISSED, _found),
    ):
        print(title)
        for left, right in pairs:
            score = _score(encoder, left, right)
            _print(score, mark(score), left, right)


if __name__ == "__main__":
    main()
