"""The search index and `query_data` with `search`: ranking, keeping current, and scope."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from database_key import keyed
from scored_encoder import Scored

from llm_gateway import ToolCall
from safwa.bootstrap.modules import AI_VIEWS, ALLOWED_VIEWS, TEXT_MODEL, WORD_FORMS
from safwa.features.cards.use_cases import create_card, delete_one_card, edit_card_text
from safwa.features.tags.use_cases import create_tag
from tg_agent_shell.ai.sql import ReadOnlyQueryRunner, read_query
from tg_agent_shell.proposals.api import SimilarItems
from tg_agent_shell.registry import _similar_is_searchable
from tg_agent_shell.search.index import RRF_K, SearchIndex
from tg_agent_shell.search.words import SnowballWordForms

TZ = "Europe/Istanbul"
SEARCH = "зубной врач"


def index(tmp_path, encoder=None, text_model=TEXT_MODEL, words=WORD_FORMS) -> SearchIndex:
    """The application's index over the `sessions` fixture's database."""

    def load():
        if encoder is None:
            raise OSError("This test has no text model.")
        return encoder

    return SearchIndex(keyed(tmp_path / "sessions.db"), text_model, load, words, AI_VIEWS)


async def read(tmp_path, search_index, sql, search=None, views=ALLOWED_VIEWS) -> list[dict]:
    runner = ReadOnlyQueryRunner(
        keyed(tmp_path / "sessions.db"), views, timezone=TZ, search=search_index
    )
    arguments = {"sql": sql, **({"search": search} if search else {})}
    call = ToolCall(id="read", name="query_data", arguments_json=json.dumps(arguments))
    return (await read_query(runner, call)).rows


async def relevance(tmp_path, search_index, search) -> dict[str, float | None]:
    rows = await read(
        tmp_path, search_index, "SELECT title, relevance FROM ai_cards ORDER BY id", search
    )
    return {row["title"]: row["relevance"] for row in rows}


async def actions(sessions, *titles: str, **notes: str) -> dict[str, int]:
    async with sessions() as session:
        made = {
            title: (await create_card(session, kind="action", title=title, note=notes.get(title, ""))).id
            for title in titles
        }
        await session.commit()
    return made


def indexed(tmp_path) -> list[tuple[str, int, str]]:
    connection = keyed(tmp_path / "sessions.db").connect(read_only=True)
    try:
        return connection.execute(
            "SELECT item_type, item_id, field FROM search_entries ORDER BY id"
        ).fetchall()
    finally:
        connection.close()


async def test_ag_search_058_rows_rank_by_word_stems_and_meaning_joined_by_place(
    sessions, tmp_path
):
    """AG-SEARCH-058 — tests/brd/tg_agent_shell/agents.feature"""
    both, meaning, words, neither = (
        "Зубной врач в среду",
        "Записаться к стоматологу",
        "Позвонить",
        "Купить молоко",
    )
    below, above = "Почистить зубы", "Купить флосс"
    await actions(sessions, both, meaning, words, neither, below, above, **{words: "Про зубной протез"})
    encoder = Scored(
        SEARCH,
        {
            both: 0.8,
            meaning: 0.62,
            below: TEXT_MODEL.related - 0.001,
            above: TEXT_MODEL.related + 0.001,
        },
    )
    search_index = index(tmp_path, encoder)
    await search_index.load()

    ranked = await relevance(tmp_path, search_index, SEARCH)

    # First by stems and first by meaning; second by each; third by meaning alone.
    assert ranked[both] == pytest.approx(2 / (RRF_K + 1))
    assert ranked[meaning] == pytest.approx(1 / (RRF_K + 2))
    assert ranked[words] == pytest.approx(1 / (RRF_K + 2))
    assert ranked[above] == pytest.approx(1 / (RRF_K + 3))
    assert ranked[below] is None
    assert ranked[neither] is None
    rows = await read(
        tmp_path,
        search_index,
        "SELECT title FROM ai_cards ORDER BY relevance DESC, id LIMIT 3",
        SEARCH,
    )
    assert [row["title"] for row in rows] == [both, meaning, words]


async def test_ag_search_058_a_read_without_search_and_views_it_does_not_read_rank_nothing(
    sessions, tmp_path
):
    """AG-SEARCH-058 — tests/brd/tg_agent_shell/agents.feature"""
    await actions(sessions, "Зубной врач")
    async with sessions() as session:
        await create_tag(session, "Зубной")
        await session.commit()
    search_index = index(tmp_path)

    assert (await relevance(tmp_path, search_index, None)) == {"Зубной врач": None}
    assert (await relevance(tmp_path, search_index, SEARCH))["Зубной врач"] is not None
    # Only the Cards a read named were brought into the index, and ranked.
    assert {item_type for item_type, _, _ in indexed(tmp_path)} == {"card"}
    tags = await read(tmp_path, search_index, "SELECT name, relevance FROM ai_tags")
    assert {row["name"]: row["relevance"] for row in tags}["Зубной"] is None


async def test_ag_search_058_every_searchable_view_selects_relevance_and_its_fields(
    sessions, tmp_path
):
    """AG-SEARCH-058 — tests/brd/tg_agent_shell/agents.feature"""
    search_index = index(tmp_path)
    searchable = [view for view in AI_VIEWS if view.searchable is not None]

    assert {view.searchable.item_type for view in searchable} == {
        "card", "check", "value", "tag", "request", "reminder", "diary",
    }
    for view in searchable:
        columns = ", ".join(view.searchable.fields)
        rows = await read(
            tmp_path, search_index, f"SELECT relevance, {columns} FROM {view.name} LIMIT 1", "x"
        )
        assert not rows or "status" not in rows[0], (view.name, rows)


def test_ag_search_058_a_word_matches_by_its_stem_in_its_own_script():
    """AG-SEARCH-058 — tests/brd/tg_agent_shell/agents.feature"""
    assert WORD_FORMS.stems("Стоматолога") == WORD_FORMS.stems("стоматолог")
    assert WORD_FORMS.stems("Running shoes") == ["run", "shoe"]
    # A script with no stemmer named, or no letter at all, is kept as written.
    assert WORD_FORMS.stems("Καλημέρα 42") == ["καλημέρα", "42"]
    assert SnowballWordForms({}).stems("Стоматолога") == ["стоматолога"]
    assert WORD_FORMS.name != SnowballWordForms({}).name


async def test_ag_search_059_the_index_follows_every_change(sessions, tmp_path):
    """AG-SEARCH-059 — tests/brd/tg_agent_shell/agents.feature"""
    card_id = (await actions(sessions, "Купить молоко"))["Купить молоко"]
    search_index = index(tmp_path)
    assert (await relevance(tmp_path, search_index, "молока"))["Купить молоко"] is not None

    async with sessions() as session:
        await edit_card_text(session, card_id, "title", "Купить хлеб")
        await session.commit()
    assert (await relevance(tmp_path, search_index, "молока"))["Купить хлеб"] is None
    assert (await relevance(tmp_path, search_index, "хлеба"))["Купить хлеб"] is not None

    async with sessions() as session:
        await delete_one_card(session, card_id)
        await session.commit()
    assert await relevance(tmp_path, search_index, "хлеба") == {}
    assert indexed(tmp_path) == []


async def test_ag_search_059_another_model_or_word_forms_indexes_everything_again(
    sessions, tmp_path
):
    """AG-SEARCH-059 — tests/brd/tg_agent_shell/agents.feature"""
    title = "Записаться к стоматологу"
    await actions(sessions, title)
    first = Scored(SEARCH, {title: 0.9})
    await index(tmp_path, first).load()
    assert first.encoded == [title]

    # The same model again finds every text where it left it.
    await index(tmp_path, first).load()
    assert first.encoded == [title]

    other = Scored(SEARCH, {title: 0.9})
    await index(tmp_path, other, replace(TEXT_MODEL, name="another text model")).load()
    assert other.encoded == [title]

    await index(tmp_path, first, words=SnowballWordForms({})).load()
    assert first.encoded == [title, title]
    assert indexed(tmp_path) == [("card", 1, "title")]


async def test_ag_search_059_until_the_text_model_loads_a_search_goes_by_words(
    sessions, tmp_path
):
    """AG-SEARCH-059 — tests/brd/tg_agent_shell/agents.feature"""
    meaning, words = "Записаться к стоматологу", "Зубной врач"
    await actions(sessions, meaning, words)
    search_index = index(tmp_path, Scored(SEARCH, {meaning: 0.9}))

    ranked = await relevance(tmp_path, search_index, SEARCH)
    assert ranked[meaning] is None
    assert ranked[words] is not None

    await search_index.load()
    assert (await relevance(tmp_path, search_index, SEARCH))[meaning] is not None


async def test_ag_search_060_no_reader_reaches_the_index(sessions, tmp_path):
    """AG-SEARCH-060 — tests/brd/tg_agent_shell/agents.feature"""
    await actions(sessions, "Зубной врач")
    search_index = index(tmp_path)
    await relevance(tmp_path, search_index, SEARCH)

    for table in ("search_entries", "search_fts"):
        [refused] = await read(tmp_path, search_index, f"SELECT * FROM {table}")
        assert refused["code"] == "unsafe_query"
        assert refused["retryable"] is True


async def test_a_reader_with_no_index_is_told_to_filter_instead(sessions, tmp_path):
    [refused] = await read(tmp_path, None, "SELECT id FROM ai_cards", SEARCH)

    assert refused["retryable"] is True
    assert "filter with WHERE" in refused["error"]


def test_pr_similar_030_a_creating_screen_compares_only_a_field_the_index_holds():
    """PR-SIMILAR-030 — tests/brd/tg_agent_shell/proposals.feature"""

    async def nothing_open(_session):
        return []

    _similar_is_searchable({"tag": SimilarItems(field="name", open_items=nothing_open)}, AI_VIEWS)
    with pytest.raises(RuntimeError, match="no searchable view indexes"):
        _similar_is_searchable(
            {"tag": SimilarItems(field="colour", open_items=nothing_open)}, AI_VIEWS
        )
