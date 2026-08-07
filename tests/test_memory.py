from __future__ import annotations

import pytest

from safwa.memory import MemoryFileError, MemoryFileStore
from safwa.models import MemoryFactCache


async def test_memory_file_is_authoritative_and_external_edits_sync(sessions, tmp_path):
    path = tmp_path / "memory.md"
    path.write_text("Likes morning walks.\nValues honest work.\n", encoding="utf-8")
    store = MemoryFileStore(path, sessions, token_budget=4000)
    snapshot = await store.sync()
    assert snapshot.facts == ("Likes morning walks.", "Values honest work.")

    path.write_text("Prefers evening planning.\n", encoding="utf-8")
    snapshot = await store.sync()
    assert snapshot.facts == ("Prefers evening planning.",)
    async with sessions() as session:
        assert (await session.get(MemoryFactCache, 1)).fact == "Prefers evening planning."
        assert await session.get(MemoryFactCache, 2) is None


async def test_memory_atomic_update_rejects_stale_hash(sessions, tmp_path):
    path = tmp_path / "memory.md"
    path.write_text("First fact.\n", encoding="utf-8")
    store = MemoryFileStore(path, sessions)
    snapshot = await store.sync()
    path.write_text("Local edit.\n", encoding="utf-8")
    with pytest.raises(MemoryFileError):
        await store.replace_facts(["AI edit."], expected_hash=snapshot.file_hash)
    assert path.read_text(encoding="utf-8") == "Local edit.\n"


async def test_blank_line_disables_memory_without_overwrite(sessions, tmp_path):
    path = tmp_path / "memory.md"
    original = "One.\n\nTwo.\n"
    path.write_text(original, encoding="utf-8")
    store = MemoryFileStore(path, sessions)
    snapshot = await store.sync()
    assert not snapshot.valid
    assert snapshot.text == ""
    assert path.read_text(encoding="utf-8") == original
