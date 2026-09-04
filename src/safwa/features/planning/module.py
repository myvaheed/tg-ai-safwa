"""Planning: the mode the workspace is in when no Sprint runs, and the Sprint itself."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from . import background, telegram, views
from .telegram import PLANNING_CALLBACK_ACTIONS, render_sprint

MODULE = FeatureModule(
    name="planning",
    views=views.VIEWS,
    background=(background.SPRINT_EXPIRY,),
    commands=(
        ScreenCommand(
            handler=render_sprint,
            command="sprint",
            description="Planning or Sprint",
            nav="sprint",
            title="🏃 Sprint",
        ),
    ),
    callback_actions=PLANNING_CALLBACK_ACTIONS,
    text_inputs=(telegram.TEXT_INPUT,),
    start_links=(telegram.PLAN_LINK,),
)
