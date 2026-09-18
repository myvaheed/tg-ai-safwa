"""Planning: the mode the workspace is in when no Sprint runs, and the Sprint itself."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from . import telegram, views
from .hooks import KEY_ACTIONS_HOOK as KEY_ACTIONS_HOOK
from .hooks import KEY_WARNING_HOOK as KEY_WARNING_HOOK
from .hooks import SPRINT_EXPIRY_HOOK as SPRINT_EXPIRY_HOOK
from .hooks import SPRINT_SUMMARY_HOOK as SPRINT_SUMMARY_HOOK
from .hooks import expire_due_sprint_now
from .telegram import PLANNING_CALLBACK_ACTIONS, render_sprint

MODULE = FeatureModule(
    name="planning",
    views=views.VIEWS,
    # A midnight Safwa was not running for is made up here, on the next start.
    recover=expire_due_sprint_now,
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
