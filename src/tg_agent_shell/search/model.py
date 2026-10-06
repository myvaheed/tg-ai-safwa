"""The search index's two tables: every searchable text with its meaning, and its word stems."""

from __future__ import annotations

from sqlalchemy import DDL, Integer, LargeBinary, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from ..foundation.models import Base

FTS_TABLE = "search_fts"


class SearchEntry(Base):
    """One text of one item: which field it is, the hash it was indexed under, and its vector.

    `search_fts` holds the same text's stems under this row's id. A contentless FTS5 table
    keeps nothing else, so `SearchIndex.refresh` writes and deletes the two together. No
    `ai_*` view names either table: the shell reads them, and no reader's SQL does.
    """

    __tablename__ = "search_entries"
    __table_args__ = (UniqueConstraint("item_type", "item_id", "field", "text_hash"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_type: Mapped[str] = mapped_column(String(40))
    item_id: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(80))
    text_hash: Mapped[str] = mapped_column(String(64))
    # The text's unit vector as little-endian float32; NULL until the text model has loaded.
    vector: Mapped[bytes | None] = mapped_column(LargeBinary)


# Built with its companion, so `upgrade_database` creates both or neither.
event.listen(
    SearchEntry.__table__,
    "after_create",
    DDL(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(stems, content='', "
        "contentless_delete=1, tokenize='unicode61 remove_diacritics 2')"
    ),
)
