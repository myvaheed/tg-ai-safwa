"""A Tag's Telegram adapter: the list, the screen, the editor and the review screen."""

from __future__ import annotations

from .review import TagProposalPresenter, tag_citation_label
from .screens import (
    TAG_CALLBACK_ACTIONS,
    TEXT_INPUT,
    command_tags,
    open_tag,
    render_tag,
)

__all__ = [
    "TAG_CALLBACK_ACTIONS",
    "TEXT_INPUT",
    "TagProposalPresenter",
    "command_tags",
    "open_tag",
    "render_tag",
    "tag_citation_label",
]
