from __future__ import annotations

import pytest

from safwa.features.continuity.memory import MemoryFileError, MemoryFileStore
from safwa.features.continuity.model import MemorySyncState

NOT_TEXT = b"\xff\xfe not text at all"


async def test_memory_store_reads_non_empty_lines_from_real_text_file(sessions, tmp_path):
    """CO-MEMORY-004 — tests/brd/continuity.feature"""
    path = tmp_path / "memory.md"
    store = MemoryFileStore(path, sessions, token_budget=4000)
    assert (await store.sync()).facts == ()

    path.write_text(" Likes morning walks. \n\nValues honest work.\n", encoding="utf-8")
    snapshot = await store.sync()
    assert snapshot.facts == ("Likes morning walks.", "Values honest work.")


async def test_next_sync_observes_a_local_memory_edit(sessions, tmp_path):
    """CO-MEMORY-005 — tests/brd/continuity.feature"""
    path = tmp_path / "memory.md"
    path.write_text("Likes morning walks.\n", encoding="utf-8")
    store = MemoryFileStore(path, sessions, token_budget=4000)
    assert (await store.sync()).facts == ("Likes morning walks.",)

    path.write_text("Prefers evening planning.\n", encoding="utf-8")
    assert (await store.sync()).facts == ("Prefers evening planning.",)


async def test_ai_memory_write_rejects_a_stale_file_hash(sessions, tmp_path):
    """CO-MEMORY-006 — tests/brd/continuity.feature"""
    path = tmp_path / "memory.md"
    path.write_text("First fact.\n", encoding="utf-8")
    store = MemoryFileStore(path, sessions)
    snapshot = await store.sync()
    path.write_text("Local edit.\n", encoding="utf-8")
    with pytest.raises(MemoryFileError):
        await store.replace_facts(["AI edit."], expected_hash=snapshot.file_hash)
    assert path.read_text(encoding="utf-8") == "Local edit.\n"


async def test_unreadable_memory_file_yields_no_facts_and_a_reason(sessions, tmp_path):
    """CO-MEMORY-012 — tests/brd/continuity.feature"""
    path = tmp_path / "memory.md"
    path.write_bytes(NOT_TEXT)
    store = MemoryFileStore(path, sessions)

    snapshot = await store.sync()

    assert snapshot.facts == ()
    assert snapshot.text == ""
    assert snapshot.error is not None and "UTF-8" in snapshot.error
    assert path.read_bytes() == NOT_TEXT
    async with sessions() as session:
        state = await session.get(MemorySyncState, 1)
    assert state.error == snapshot.error


async def test_memory_over_the_token_budget_is_not_injected(sessions, tmp_path):
    """CO-MEMORY-012 — tests/brd/continuity.feature"""
    path = tmp_path / "memory.md"
    written = "A durable fact.\n" * 500
    path.write_text(written, encoding="utf-8")
    store = MemoryFileStore(path, sessions, token_budget=10)

    snapshot = await store.sync()

    assert snapshot.facts == ()
    assert snapshot.text == ""
    assert snapshot.error is not None and "token" in snapshot.error
    assert path.read_text(encoding="utf-8") == written


async def test_a_manual_fact_is_appended_to_the_file(sessions, tmp_path):
    """CO-MEMORY-014 — tests/brd/continuity.feature"""
    path = tmp_path / "memory.md"
    path.write_text("Likes morning walks.\nValues honest work.\n", encoding="utf-8")
    store = MemoryFileStore(path, sessions)

    snapshot = await store.append_manual("  Plans on Sunday evenings.  ")

    assert snapshot.facts == (
        "Likes morning walks.",
        "Values honest work.",
        "Plans on Sunday evenings.",
    )
    assert path.read_text(encoding="utf-8") == (
        "Likes morning walks.\nValues honest work.\nPlans on Sunday evenings.\n"
    )


async def test_a_manual_fact_is_refused_when_the_file_could_not_be_read(sessions, tmp_path):
    """CO-MEMORY-014 — tests/brd/continuity.feature"""
    path = tmp_path / "memory.md"
    path.write_bytes(NOT_TEXT)
    store = MemoryFileStore(path, sessions)

    with pytest.raises(MemoryFileError):
        await store.append_manual("Plans on Sunday evenings.")

    assert path.read_bytes() == NOT_TEXT
