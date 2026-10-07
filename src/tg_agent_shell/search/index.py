"""The search index: kept current from the searchable views, and asked two questions.

`relevance` ranks the rows of a read that searches, by words and by meaning (AG-SEARCH-058).
`alike` lists the open items a new one repeats on its creating screen (PR-SIMILAR-030). Both
first `refresh` the item types they read, which compares the words each item has now with what
the index holds (AG-SEARCH-059). How the pieces fit is docs/SEARCH.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Collection, Iterable, Sequence

import numpy

from ..ai.sql import SqlView
from ..foundation.database import DatabaseFile
from .model import FTS_TABLE
from .words import TextEncoder, TextModel, WordForms

logger = logging.getLogger(__name__)

# Reciprocal rank fusion: a row's relevance is 1/(RRF_K + its place) by words plus the same by
# meaning. 60 is the constant the method was published with; it keeps one first place from
# outweighing a row that is near the top of both orders.
RRF_K = 60

Key = tuple[str, int]


def _unit(vector: Sequence[float]) -> numpy.ndarray:
    array = numpy.asarray(vector, dtype="<f4")
    length = float(numpy.linalg.norm(array)) or 1.0
    return array / length


def _places(scores: dict[Key, float], *, highest_first: bool) -> dict[Key, int]:
    ordered = sorted(scores, key=lambda key: -scores[key] if highest_first else scores[key])
    return {key: place for place, key in enumerate(ordered, start=1)}


class SearchIndex:
    """Every searchable text of an application, with its meaning and its word stems.

    One lock covers every refresh and every question: the text model runs one call at a time,
    and a question asked while the index is being rebuilt waits for it rather than ranking
    half an index.
    """

    def __init__(
        self,
        database: DatabaseFile,
        text_model: TextModel,
        load: Callable[[], TextEncoder],
        words: WordForms,
        views: Iterable[SqlView],
    ) -> None:
        self.database = database
        self.text_model = text_model
        self.words = words
        self._load = load
        self._encoder: TextEncoder | None = None
        self._lock = asyncio.Lock()
        # By item type: the table that holds those items' words, and its searchable fields.
        self._sources: dict[str, tuple[str, tuple[str, ...]]] = {}
        self._types_by_view: dict[str, str] = {}
        for view in views:
            if view.searchable is None:
                continue
            item_type = view.searchable.item_type
            if item_type in self._sources:
                raise RuntimeError(f"Two views are searchable as {item_type!r}")
            self._sources[item_type] = (view.searchable.table, view.searchable.fields)
            self._types_by_view[view.name] = item_type

    def fields(self, item_type: str) -> tuple[str, ...]:
        """The searchable fields of one item type; none for a type nothing indexes."""
        source = self._sources.get(item_type)
        return source[1] if source else ()

    async def load(self) -> None:
        """Load the text model off the event loop, then index everything. Nothing awaits
        this, so a failure is logged: one to load goes on by words alone, and one to index
        is tried again by the next search."""
        try:
            self._encoder = await asyncio.to_thread(self._load)
        except Exception:
            logger.exception("The text model did not load; search goes by words alone")
            return
        try:
            await self.refresh()
        except Exception:
            logger.exception("The first indexing failed; the next search tries again")

    async def refresh(self, item_types: Collection[str] | None = None) -> None:
        """Bring the named item types, or all of them, up to the words their items have now."""
        async with self._lock:
            await asyncio.to_thread(self._refresh, item_types)

    async def relevance(self, search: str, views: Collection[str]) -> dict[Key, float]:
        """Each item the named views list that is close to `search` by words or by meaning,
        with its relevance; an item close by neither is absent."""
        item_types = {self._types_by_view[name] for name in views if name in self._types_by_view}
        if not search.strip() or not item_types:
            return {}
        async with self._lock:
            await asyncio.to_thread(self._refresh, item_types)
            return await asyncio.to_thread(self._relevance, search, item_types)

    async def alike(
        self, item_type: str, field: str, words: str, candidates: Collection[int]
    ) -> list[int]:
        """The candidates whose `field` is at least `TextModel.alike` close in meaning to
        `words`, closest first. Nothing until the text model has loaded, or once it failed."""
        if self._encoder is None or not words.strip() or not candidates:
            return []
        try:
            async with self._lock:
                await asyncio.to_thread(self._refresh, (item_type,))
                return await asyncio.to_thread(
                    self._alike, item_type, field, words, frozenset(candidates)
                )
        except Exception:
            logger.exception("The similar items search failed; this screen goes without it")
            return []

    # ------------------------------------------------------------- off the event loop

    def _hash(self, text: str) -> str:
        model = self.text_model
        key = f"{model.name}\0{model.text_prompt}\0{self.words.name}\0{text}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _encode(self, texts: Sequence[str], prompt: str) -> list[numpy.ndarray] | None:
        """Unit vectors, or None while there is no text model or once it failed."""
        if self._encoder is None or not texts:
            return None
        try:
            return [_unit(vector) for vector in self._encoder.encode([prompt + t for t in texts])]
        except Exception:
            logger.exception("The text model failed; these texts wait for the next refresh")
            return None

    def _refresh(self, item_types: Collection[str] | None) -> None:
        named = self._sources if item_types is None else item_types
        types = [item_type for item_type in named if item_type in self._sources]
        if not types:
            return
        connection = self.database.connect()
        try:
            for item_type in types:
                self._refresh_type(connection, item_type)
            connection.commit()
        finally:
            connection.close()

    def _refresh_type(self, connection, item_type: str) -> None:  # type: ignore[no-untyped-def]
        table, fields = self._sources[item_type]
        columns = ", ".join(f'"{field}"' for field in fields)
        wanted: dict[tuple[int, str, str], str] = {}
        for item_id, *values in connection.execute(f'SELECT id, {columns} FROM "{table}"'):
            for field, value in zip(fields, values, strict=True):
                text = str(value).strip() if value is not None else ""
                if text:
                    wanted[(item_id, field, self._hash(text))] = text
        held: dict[tuple[int, str, str], tuple[int, bool]] = {
            (item_id, field, text_hash): (entry_id, vector is not None)
            for entry_id, item_id, field, text_hash, vector in connection.execute(
                "SELECT id, item_id, field, text_hash, vector FROM search_entries "
                "WHERE item_type = ?",
                (item_type,),
            )
        }
        missing = [key for key in wanted if key not in held]
        unmeant = missing + [
            key for key, (_, has_vector) in held.items() if key in wanted and not has_vector
        ]
        # Encoded before anything is written, so the write lock is held only for the writes.
        vectors = self._encode([wanted[key] for key in unmeant], self.text_model.text_prompt) or []
        meant = dict(zip(unmeant, vectors, strict=False))
        stale = [entry_id for key, (entry_id, _) in held.items() if key not in wanted]
        for entry_id in stale:
            connection.execute("DELETE FROM search_entries WHERE id = ?", (entry_id,))
            connection.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid = ?", (entry_id,))
        for key in missing:
            item_id, field, text_hash = key
            vector = meant.get(key)
            cursor = connection.execute(
                "INSERT INTO search_entries (item_type, item_id, field, text_hash, vector) "
                "VALUES (?, ?, ?, ?, ?)",
                (item_type, item_id, field, text_hash, None if vector is None else vector.tobytes()),
            )
            connection.execute(
                f"INSERT INTO {FTS_TABLE} (rowid, stems) VALUES (?, ?)",
                (cursor.lastrowid, " ".join(self.words.stems(wanted[key]))),
            )
        for key, (entry_id, has_vector) in held.items():
            if key in meant and not has_vector:
                connection.execute(
                    "UPDATE search_entries SET vector = ? WHERE id = ?",
                    (meant[key].tobytes(), entry_id),
                )

    def _vectors(self, connection, item_types: Collection[str], field: str | None = None):  # type: ignore[no-untyped-def]
        marks = ", ".join("?" for _ in item_types)
        sql = (
            "SELECT item_type, item_id, vector FROM search_entries "
            f"WHERE item_type IN ({marks}) AND vector IS NOT NULL"
        )
        parameters: list[object] = list(item_types)
        if field is not None:
            sql += " AND field = ?"
            parameters.append(field)
        return connection.execute(sql, parameters).fetchall()

    def _closest(
        self, connection, item_types: Collection[str], text: str, prompt: str,  # type: ignore[no-untyped-def]
        field: str | None = None,
    ) -> dict[Key, float]:
        """Each item's best cosine to `text`, encoded after `prompt`, over its rows."""
        encoded = self._encode([text], prompt)
        rows = self._vectors(connection, item_types, field) if encoded else []
        if not rows:
            return {}
        matrix = numpy.stack([numpy.frombuffer(vector, dtype="<f4") for _, _, vector in rows])
        scores = matrix @ encoded[0]
        best: dict[Key, float] = {}
        for (item_type, item_id, _), score in zip(rows, scores, strict=True):
            key = (item_type, item_id)
            best[key] = max(best.get(key, -1.0), float(score))
        return best

    def _by_words(self, connection, item_types: Collection[str], search: str) -> dict[Key, float]:  # type: ignore[no-untyped-def]
        """Each item's best BM25 over its rows; lower is closer, as FTS5 counts it."""
        stems = list(dict.fromkeys(self.words.stems(search)))
        if not stems:
            return {}
        marks = ", ".join("?" for _ in item_types)
        rows = connection.execute(
            f"SELECT e.item_type, e.item_id, bm25({FTS_TABLE}) FROM {FTS_TABLE} "
            f"JOIN search_entries e ON e.id = {FTS_TABLE}.rowid "
            f"WHERE {FTS_TABLE} MATCH ? AND e.item_type IN ({marks})",
            [" OR ".join(f'"{stem}"' for stem in stems), *item_types],
        )
        best: dict[Key, float] = {}
        for item_type, item_id, score in rows:
            key = (item_type, item_id)
            best[key] = min(best.get(key, score), score)
        return best

    def _relevance(self, search: str, item_types: Collection[str]) -> dict[Key, float]:
        connection = self.database.connect(read_only=True)
        try:
            by_words = _places(self._by_words(connection, item_types, search), highest_first=False)
            meaning = {
                key: score
                for key, score in self._closest(
                    connection, item_types, search, self.text_model.search_prompt
                ).items()
                if score >= self.text_model.related
            }
            by_meaning = _places(meaning, highest_first=True)
        finally:
            connection.close()
        return {
            key: sum(1 / (RRF_K + places[key]) for places in (by_words, by_meaning) if key in places)
            for key in by_words.keys() | by_meaning.keys()
        }

    def _alike(self, item_type: str, field: str, words: str, candidates: frozenset[int]) -> list[int]:
        connection = self.database.connect(read_only=True)
        try:
            scores = self._closest(
                connection, (item_type,), words, self.text_model.text_prompt, field
            )
        finally:
            connection.close()
        close = [
            (score, item_id)
            for (_, item_id), score in scores.items()
            if item_id in candidates and score >= self.text_model.alike
        ]
        return [item_id for _, item_id in sorted(close, key=lambda pair: -pair[0])]
