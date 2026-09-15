"""The heavy analyzer: the helper the root session calls when one read will not answer."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule, HelperSpec

from .agent import NAME, PROMPT_TEMPLATE, VIEWS, build
from .hooks import HEAVY_ANALYZER_HOOK as HEAVY_ANALYZER_HOOK

MODULE = FeatureModule(
    name=NAME,
    helpers=(
        HelperSpec(
            name=NAME,
            instructions=PROMPT_TEMPLATE,
            build=build,
            views=VIEWS,
        ),
    ),
)
