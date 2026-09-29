"""Retro: the screens over the Sprints that have closed, and the subagent that answers
from their records. No entity of its own: what it writes, it writes on the Sprint's row."""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from ..planning.model import Sprint
from . import agent, telegram

MODULE = FeatureModule(
    name="retro",
    agents=(agent.RETRO_AGENT,),
    screens=(
        ScreenSpec(
            item_type="retro",
            model=Sprint,
            open=telegram.open_retro,
            label=telegram.retro_citation_label,
        ),
    ),
    commands=(
        ScreenCommand(
            handler=telegram.render_retro_list,
            command="retro",
            description="Ended Sprints and their retros",
            nav="retro",
            title="📊 Retro",
        ),
    ),
    callback_actions=telegram.RETRO_CALLBACK_ACTIONS,
)
