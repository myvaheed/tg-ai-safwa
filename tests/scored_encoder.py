"""A text model whose closeness a test states outright."""

from __future__ import annotations

import math


class Scored:
    """An encoder whose texts are exactly as close to `to` as `scores` says.

    `to` lies on one axis. A scored text leans off it onto an axis of its own, so two texts
    other than `to` share nothing but that lean; a text not scored shares nothing at all.
    """

    def __init__(self, to: str, scores: dict[str, float]) -> None:
        self.to = to
        self.scores = scores
        self.axes: dict[str, int] = {to: 0}
        self.encoded: list[str] = []

    def encode(self, texts):
        self.encoded.extend(texts)
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * 128
        if text == self.to:
            vector[0] = 1.0
            return vector
        score = self.scores.get(text, 0.0)
        vector[0] = score
        vector[self.axes.setdefault(text, len(self.axes))] = math.sqrt(1 - score * score)
        return vector
