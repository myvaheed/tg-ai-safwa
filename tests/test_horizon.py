from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_gateway.horizon import HorizonPiiFinder, decode, discrete_gpu
from safwa.bootstrap.modules import BIRTH_WORDS, PII_KEPT

MODELS = Path(__file__).parents[1] / "data" / "models"


def _tagged(text: str, *pieces: tuple[str, str]) -> tuple[list[tuple[int, int]], list[str]]:
    """Offsets and labels for the given pieces, each the next occurrence after the last."""
    offsets, labels, position = [], [], 0
    for piece, label in pieces:
        start = text.index(piece, position)
        offsets.append((start, start + len(piece)))
        labels.append(label)
        position = start + len(piece)
    return offsets, labels


def _decoded(text: str, *pieces: tuple[str, str]) -> list[tuple[str, str]]:
    spans = decode(text, *_tagged(text, *pieces), kept=PII_KEPT, birth_words=BIRTH_WORDS)
    return [(text[span.start : span.end], span.kind) for span in spans]


def test_a_tagged_piece_is_hidden_with_the_rest_of_its_word():
    text = "Пиши мне в @annka_m, это ООО «Ромашка»."

    assert _decoded(text, ("annka", "B-USERNAME"), ("Ромашка", "B-ORGANIZATION")) == [
        ("@annka_m", "USERNAME"),
        ("Ромашка", "ORGANIZATION"),
    ]


def test_a_name_continues_across_its_words_and_two_names_stay_two():
    text = "Ольге Смирновой привет, Анна и Мария тоже."

    assert _decoded(
        text,
        ("Ольге", "B-PERSON"),
        ("Смирновой", "I-PERSON"),
        ("Анна", "B-PERSON"),
        ("Мария", "B-PERSON"),
    ) == [("Ольге Смирновой", "PERSON"), ("Анна", "PERSON"), ("Мария", "PERSON")]


def test_ag_privacy_066_places_and_dates_stay_and_a_date_of_birth_is_hidden():
    """AG-PRIVACY-066 — tests/brd/tg_agent_shell/agents.feature"""
    text = "Сдать отчёт до 15 ноября в Казани. Лена родилась 12.03.1988, д.р. 3 марта."

    assert _decoded(
        text,
        ("15", "B-DATE"),
        ("ноября", "I-DATE"),
        ("Казани", "B-LOCATION"),
        ("12.03", "B-DATE"),
        (".1988", "I-DATE"),
        ("3", "B-DATE"),
        ("марта", "I-DATE"),
    ) == [("12.03.1988", "DATE_OF_BIRTH"), ("3 марта", "DATE_OF_BIRTH")]


def _device(ep_name: str, **metadata: str) -> SimpleNamespace:
    return SimpleNamespace(ep_name=ep_name, device=SimpleNamespace(metadata=metadata))


CPU = _device("CPUExecutionProvider", Description="13th Gen Intel(R) Core(TM) i9-13900K")
BUILT_IN = _device(
    "DmlExecutionProvider",
    Description="Intel(R) UHD Graphics 770",
    Discrete="0",
    DxgiAdapterNumber="1",
    DxgiHighPerformanceIndex="2",
)


def test_ag_privacy_070_the_card_the_system_ranks_first_is_taken():
    """AG-PRIVACY-070 — tests/brd/tg_agent_shell/agents.feature"""
    devices = [
        CPU,
        _device(
            "DmlExecutionProvider",
            Description="NVIDIA RTX 4000 SFF Ada Generation",
            Discrete="1",
            DxgiAdapterNumber="2",
            DxgiHighPerformanceIndex="1",
        ),
        BUILT_IN,
        _device(
            "DmlExecutionProvider",
            Description="NVIDIA GeForce RTX 5090",
            Discrete="1",
            DxgiAdapterNumber="0",
            DxgiHighPerformanceIndex="0",
        ),
    ]

    assert discrete_gpu(devices) == (0, "NVIDIA GeForce RTX 5090")


def test_ag_privacy_070_a_built_in_card_leaves_the_work_to_the_processor():
    """AG-PRIVACY-070 — tests/brd/tg_agent_shell/agents.feature"""
    assert discrete_gpu([CPU, BUILT_IN]) is None
    assert discrete_gpu([CPU]) is None


SENTENCES = (
    "Позвонить Ольге Смирновой по номеру +7 916 123-45-67 до пятницы.",
    "Написать на olga_smirnova88@gmail.com про отчёт по Цели.",
    "Оплатить подписку картой 4276 3800 1234 5678 до конца месяца.",
    "Ключ OpenRouter: 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.",
    "Лена родилась 12.03.1988, летом едем в Казань, дедлайн 20 декабря.",
)


@pytest.fixture(scope="module")
def finder() -> HorizonPiiFinder:
    return HorizonPiiFinder.load(MODELS, kept=PII_KEPT, birth_words=BIRTH_WORDS)


def _hidden(finder: HorizonPiiFinder, text: str) -> list[tuple[str, str]]:
    return [(text[span.start : span.end], span.kind) for span in finder.find(text)]


@pytest.mark.pii_model
def test_ag_privacy_066_the_model_hides_identifiers_and_leaves_places_and_dates(finder):
    """AG-PRIVACY-066 — tests/brd/tg_agent_shell/agents.feature"""
    hidden = [_hidden(finder, text) for text in SENTENCES]

    assert hidden == [
        [("Ольге Смирновой", "PERSON"), ("+7 916 123-45-67", "PHONE")],
        [("olga_smirnova88@gmail.com", "EMAIL")],
        [("4276 3800 1234 5678", "CREDIT_CARD")],
        [("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", "SECRET")],
        [("Лена", "PERSON"), ("12.03.1988", "DATE_OF_BIRTH")],
    ]


@pytest.mark.pii_model
def test_ag_privacy_070_the_card_finds_what_the_processor_finds(finder, monkeypatch):
    """AG-PRIVACY-070 — tests/brd/tg_agent_shell/agents.feature"""
    import onnxruntime

    if finder.device == "CPU":
        pytest.skip("this computer has no discrete graphics card")
    monkeypatch.setattr(onnxruntime, "get_ep_devices", lambda: [])
    processor = HorizonPiiFinder.load(MODELS, kept=PII_KEPT, birth_words=BIRTH_WORDS)
    # Past one window, so the overlap is read on both.
    long_text = " ".join(SENTENCES * 12)

    assert processor.device == "CPU"
    for text in (*SENTENCES, long_text):
        assert finder.find(text) == processor.find(text)
