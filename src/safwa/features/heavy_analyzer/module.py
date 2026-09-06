"""The heavy analyzer: the helper the root session calls when one read will not answer."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule, HelperSpec

from .agent import NAME, OFFER, PROMPT_TEMPLATE, VIEWS, build, worth_a_helper

MODULE = FeatureModule(
    name=NAME,
    helpers=(
        HelperSpec(
            name=NAME,
            instructions=PROMPT_TEMPLATE,
            build=build,
            offer_when=worth_a_helper,
            offer=OFFER,
            views=VIEWS,
        ),
    ),
)
