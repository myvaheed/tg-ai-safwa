from __future__ import annotations

import pytest

from safwa.features.continuity.api import MemoryFileError, MemoryFileStore


async def test_memory_store_reads_non_empty_lines_from_real_text_file(sessions, tmp_path):
    """CO-MEMORY-004: sync reads a real tmp_path file; blank lines contain no fact."""
    path = tmp_path / "memory.md"
    store = MemoryFileStore(path, sessions, token_budget=4000)
    assert (await store.sync()).facts == ()

    path.write_text(" Likes morning walks. \n\nValues honest work.\n", encoding="utf-8")
    snapshot = await store.sync()
    assert snapshot.facts == ("Likes morning walks.", "Values honest work.")


async def test_next_sync_observes_a_local_memory_edit(sessions, tmp_path):
    """CO-MEMORY-005: an edit after the first read replaces the observed facts."""
    path = tmp_path / "memory.md"
    path.write_text("Likes morning walks.\n", encoding="utf-8")
    store = MemoryFileStore(path, sessions, token_budget=4000)
    assert (await store.sync()).facts == ("Likes morning walks.",)

    path.write_text("Prefers evening planning.\n", encoding="utf-8")
    assert (await store.sync()).facts == ("Prefers evening planning.",)


async def test_ai_memory_write_rejects_a_stale_file_hash(sessions, tmp_path):
    """CO-MEMORY-006: an AI write cannot overwrite a newer local edit."""
    path = tmp_path / "memory.md"
    path.write_text("First fact.\n", encoding="utf-8")
    store = MemoryFileStore(path, sessions)
    snapshot = await store.sync()
    path.write_text("Local edit.\n", encoding="utf-8")
    with pytest.raises(MemoryFileError):
        await store.replace_facts(["AI edit."], expected_hash=snapshot.file_hash)
    assert path.read_text(encoding="utf-8") == "Local edit.\n"
