"""What a text is to the search index: its meaning as a vector, and its words as stems.

`TextEncoder` is the meaning port — texts in, one vector each out — and `FastEmbedEncoder` its
adapter: an ONNX model that `fastembed` downloads once into the directory it is given and runs
on the CPU. `WordForms` is the words port — a text in, its word stems out — and
`SnowballWordForms` its adapter. `TextModel` is the encoder an application chose, with what it
is told a text is and the two cut-offs read off it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

WORD = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class TextModel:
    """One embedding model, and the two cut-offs read off it with scripts/search_probe.py."""

    name: str
    # A creating screen lists an open item whose name is at least this alike (PR-SIMILAR-030).
    alike: float
    # A search counts a row by its meaning at least this close (AG-SEARCH-058).
    related: float
    # Written before a search, and before every text it is compared with, for a model trained
    # to tell the two apart. A new item's name on its creating screen is a text.
    search_prompt: str = ""
    text_prompt: str = ""


class TextEncoder(Protocol):
    def encode(self, texts: Sequence[str]) -> list[Sequence[float]]:
        """One vector per text, in order. It blocks, so it is called off the event loop."""


class FastEmbedEncoder:
    def __init__(self, model: str, cache_dir: Path) -> None:
        # Imported here: an application that never searches never loads the runtime.
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model, cache_dir=str(cache_dir))

    def encode(self, texts: Sequence[str]) -> list[Sequence[float]]:
        return list(self._model.embed(list(texts)))


class WordForms(Protocol):
    # Part of every indexed text's hash: other word forms make another index.
    name: str

    def stems(self, text: str) -> list[str]:
        """The text's words, each reduced to its stem, in order."""


def script(word: str) -> str:
    """The Unicode script of a word's first letter, as `cyrillic` or `latin`; "" for none."""
    for char in word:
        if char.isalpha():
            return unicodedata.name(char, "").split(" ", 1)[0].casefold()
    return ""


class SnowballWordForms:
    """A Snowball stemmer for each script the application names; a word in any other script,
    or with no letter, is kept as written. Given no scripts, every word is kept as written."""

    def __init__(self, languages: Mapping[str, str]) -> None:
        import snowballstemmer

        # By script, as `script` names it: {"cyrillic": "russian", "latin": "english"}.
        self._stemmers = {
            name: snowballstemmer.stemmer(language) for name, language in languages.items()
        }
        self.name = "snowball:" + ",".join(f"{k}={v}" for k, v in sorted(languages.items()))

    def stems(self, text: str) -> list[str]:
        words = WORD.findall(text.casefold())
        return [
            stemmer.stemWord(word) if (stemmer := self._stemmers.get(script(word))) else word
            for word in words
        ]
