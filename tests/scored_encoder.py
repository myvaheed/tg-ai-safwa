"""A text model whose closeness a test states outright."""

from __future__ import annotations

import math

from safwa.bootstrap.modules import TEXT_MODEL

PROMPTS = (TEXT_MODEL.search_prompt, TEXT_MODEL.text_prompt)


class Scored:
    """An encoder whose texts are exactly as close to `to` as `scores` says.

    `to` lies on one axis. A scored text leans off it onto an axis of its own, so two texts
    other than `to` share nothing but that lean; a text not scored shares nothing at all.
    A text is named as written: the prompt the index puts before it is taken off first, and
    `sent` keeps what the index sent.
    """

    def __init__(self, to: str, scores: dict[str, float]) -> None:
        self.to = to
        self.scores = scores
        self.axes: dict[str, int] = {to: 0}
        self.sent: list[str] = []
        self.encoded: list[str] = []

    def encode(self, texts):
        self.sent.extend(texts)
        written = [_written(text) for text in texts]
        self.encoded.extend(written)
        return [self._vector(text) for text in written]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * 128
        if text == self.to:
            vector[0] = 1.0
            return vector
        score = self.scores.get(text, 0.0)
        vector[0] = score
        vector[self.axes.setdefault(text, len(self.axes))] = math.sqrt(1 - score * score)
        return vector


def _written(text: str) -> str:
    for prompt in PROMPTS:
        if prompt and text.startswith(prompt):
            return text.removeprefix(prompt)
    return text
