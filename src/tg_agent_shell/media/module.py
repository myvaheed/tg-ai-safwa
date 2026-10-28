"""Photos: their citations, screens and proposed label changes."""

from __future__ import annotations

from ..foundation.screens import ScreenSpec
from ..telegram.manifest import FeatureModule, ProposalContribution
from .agent import RENAME_MEDIA_TOOL
from .library import MEDIA_TYPE, ChatMedia
from .proposal import MediaProposalHandler
from .telegram import MediaProposalPresenter, media_citation_label, open_media

MODULE = FeatureModule(
    name="media",
    proposals=(
        ProposalContribution(
            handler=MediaProposalHandler(),
            tool=RENAME_MEDIA_TOOL,
            presenter=MediaProposalPresenter(),
        ),
    ),
    screens=(
        ScreenSpec(
            item_type=MEDIA_TYPE,
            model=ChatMedia,
            open=open_media,
            label=media_citation_label,
        ),
    ),
)
