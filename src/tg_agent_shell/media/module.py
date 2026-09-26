"""Photos: what an application includes to cite a photo and open it again."""

from __future__ import annotations

from ..foundation.screens import ScreenSpec
from ..telegram.manifest import FeatureModule
from .library import MEDIA_TYPE, ChatMedia
from .telegram import media_citation_label, open_media

MODULE = FeatureModule(
    name="media",
    screens=(
        ScreenSpec(
            item_type=MEDIA_TYPE,
            model=ChatMedia,
            open=open_media,
            label=media_citation_label,
        ),
    ),
)
