"""Diagnostics: one command, no entity and no screen of its own."""

from __future__ import annotations

from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule

from .telegram import command_status

# Each answer ends with who wrote it: ↪️ and the subagent whose words the Advisor forwarded
# as they are, or ✍️ advisor when the Advisor wrote them itself.
SHOW_ANSWER_SOURCE = True

MODULE = FeatureModule(
    name="diagnostics",
    commands=(
        ScreenCommand(
            handler=command_status, command="status", description="Safwa diagnostics"
        ),
    ),
)
