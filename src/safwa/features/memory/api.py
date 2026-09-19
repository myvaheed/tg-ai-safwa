"""What another feature may ask of Memory."""

from __future__ import annotations

from tg_agent_shell.telegram import Services

from .absorb import PatternReviewer


def memory_reviewer(services: Services) -> PatternReviewer:
    """The one question memory asks the model, off the bag the composition root filled."""
    return services.features.memory_reviewer
