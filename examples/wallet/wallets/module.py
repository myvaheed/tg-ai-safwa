"""Wallets and categories, as this application registers them.

This is where the home screen is declared: the shell needs exactly one, and refuses a
list that has none or two.
"""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.telegram.contributions import HOME_NAV, ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from . import telegram, views
from .model import Wallet

MODULE = FeatureModule(
    name="wallets",
    views=views.VIEWS,
    screens=(
        ScreenSpec(
            item_type="wallet",
            model=Wallet,
            open=telegram.open_wallet,
            label=telegram.wallet_citation_label,
        ),
    ),
    commands=(
        ScreenCommand(
            handler=telegram.render_home,
            command="start",
            description="Open the wallets",
            nav=HOME_NAV,
        ),
    ),
    callback_actions=telegram.WALLET_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
)
