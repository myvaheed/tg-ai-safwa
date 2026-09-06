"""What another module may ask of Summary."""

from __future__ import annotations

from tg_agent_shell.telegram import Services

from .summary import DialogueSummary


def dialogue_summary(services: Services) -> DialogueSummary:
    """The one Summary writer, off the bag the composition root filled."""
    return services.features.summary
