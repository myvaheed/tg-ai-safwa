"""The one place that knows which features exist.

Adding a feature is a package under `features/` plus one line in `MODULES`. Everything
below it is derived by [Registry](../../tg_agent_shell/registry.py), which is the shell's:
the view catalogue and its allowlist, the proposal capabilities, the subagent roster and
the hooks. What stays here is what only Safwa can answer — the list itself, what a world
is, and the persona the Advisor speaks in.

`MODULES` is a constant of import time, so the assembled prompt is built once and the
cacheable prefix stays byte-identical across turns.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.ai.sql import view_catalogue
from tg_agent_shell.media.module import MODULE as MEDIA_FEATURE
from tg_agent_shell.proposals.api import World
from tg_agent_shell.proposals.hooks import AUTOAPPROVAL_HOOK, PLAN_HOOK, REQUEST_REVIEW_HOOK
from tg_agent_shell.proposals.module import MODULE as PROPOSALS_FEATURE
from tg_agent_shell.registry import Registry
from tg_agent_shell.search.words import SnowballWordForms, TextModel
from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec, FeatureModule

from .. import featuretoggles
from ..features.advisor.agent import ADVISOR_VIEWS, ITEMS, PERSONA, SYSTEM_PROMPT_TEMPLATE
from ..features.cards.module import (
    BLOCKER_HOOK,
    EFFORT_TRACKING_REMINDER_HOOK,
    EMPTY_PARENTS_HOOK,
    ENERGY_BALANCE_HOOK,
    PARENT_COMPLETION_HOOK,
    REST_TODAY_HOOK,
    SCHEDULE_PLAN_HOOK,
    TIME_TRACKING_REMINDER_HOOK,
    TODAY_MORNINGS_HOOK,
    TODAY_OVERLOAD_HOOK,
    TODAY_STALE_HOOK,
)
from ..features.cards.module import MODULE as CARDS
from ..features.checks.module import MISSED_RUN_HOOK
from ..features.checks.module import MODULE as CHECKS
from ..features.diagnostics.module import MODULE as DIAGNOSTICS
from ..features.diary.module import DIARY_HOOK, DIARY_READ_HOOK
from ..features.diary.module import MODULE as DIARY
from ..features.heavy_analyzer.module import HEAVY_ANALYZER_HOOK
from ..features.heavy_analyzer.module import MODULE as HEAVY_ANALYZER
from ..features.home.module import HOME_HOOK
from ..features.home.module import MODULE as HOME
from ..features.life.module import MODULE as LIFE
from ..features.memory.module import MEMORY_RETRO_HOOK
from ..features.memory.module import MODULE as MEMORY
from ..features.onboarding.module import MODULE as ONBOARDING
from ..features.onboarding.module import (
    NOTICE_HOOK,
    ONBOARDING_HOOK,
    PRESENCE_HOOK,
    RETURN_HOOK,
)
from ..features.planning.module import (
    KEY_ACTIONS_HOOK,
    KEY_WARNING_HOOK,
    SPRINT_END_HOOK,
    SPRINT_EXPIRY_HOOK,
    SPRINT_SUMMARY_HOOK,
)
from ..features.planning.module import MODULE as PLANNING
from ..features.profile.api import hook_switched_on
from ..features.profile.module import DAILY_SUMMARY_HOOK
from ..features.profile.module import MODULE as PROFILE
from ..features.reminders.module import MODULE as REMINDERS
from ..features.reminders.module import REMINDER_FIRE_HOOK, REMINDER_START_HOOK
from ..features.retro.module import MODULE as RETRO
from ..features.saved_requests.module import MODULE as SAVED_REQUESTS
from ..features.schedules.module import MODULE as SCHEDULES
from ..features.summary.module import MODULE as SUMMARY
from ..features.summary.module import SUMMARY_HOOK
from ..features.tags.module import MODULE as TAGS
from ..features.values.module import MODULE as VALUES
from ..features.workspace_mutator.module import MODULE as WORKSPACE_MUTATOR
from ..foundation.log_events import AI_LOG_EVENTS
from ..foundation.workspace import require_workspace

# Order is what the routing rules follow, so it is fixed rather than incidental: the
# Advisor's prompt lists the subagents in this order every run.
MODULES: tuple[FeatureModule, ...] = (
    HOME,
    WORKSPACE_MUTATOR,
    CARDS,
    CHECKS,
    VALUES,
    TAGS,
    PLANNING,
    RETRO,
    LIFE,
    DIARY,
    PROFILE,
    REMINDERS,
    SCHEDULES,
    SAVED_REQUESTS,
    PROPOSALS_FEATURE,
    MEDIA_FEATURE,
    SUMMARY,
    MEMORY,
    DIAGNOSTICS,
    HEAVY_ANALYZER,
    ONBOARDING,
)


async def _world(session: AsyncSession) -> World:
    """Safwa's answer to what a proposal is made against: the workspace row."""
    workspace = await require_workspace(session)
    return World(revision=workspace.revision, timezone=workspace.timezone)


# The automatic reactions that exist for Safwa. A hook with a switch is turned off and on
# in the Profile, which is what `hook_switched_on` reads; one that follows another's switch
# goes with it; one with neither is always on. The daily ones run at the Profile's Morning
# time. The checks on the model's own work are here only while `featuretoggles` has them on.
# A look does the work of its ticks in this order, so the Reminders come before the Home
# dashboard and memory, whose work waits for the model.
HOOKS = (
    SUMMARY_HOOK,
    HEAVY_ANALYZER_HOOK,
    BLOCKER_HOOK,
    TIME_TRACKING_REMINDER_HOOK,
    PARENT_COMPLETION_HOOK,
    EFFORT_TRACKING_REMINDER_HOOK,
    TODAY_OVERLOAD_HOOK,
    EMPTY_PARENTS_HOOK,
    SCHEDULE_PLAN_HOOK,
    ENERGY_BALANCE_HOOK,
    REST_TODAY_HOOK,
    TODAY_MORNINGS_HOOK,
    TODAY_STALE_HOOK,
    MISSED_RUN_HOOK,
    DIARY_HOOK,
    DAILY_SUMMARY_HOOK,
    SPRINT_END_HOOK,
    SPRINT_SUMMARY_HOOK,
    SPRINT_EXPIRY_HOOK,
    KEY_ACTIONS_HOOK,
    KEY_WARNING_HOOK,
    ONBOARDING_HOOK,
    NOTICE_HOOK,
    PRESENCE_HOOK,
    RETURN_HOOK,
    REMINDER_START_HOOK,
    REMINDER_FIRE_HOOK,
    HOME_HOOK,
    MEMORY_RETRO_HOOK,
    *((PLAN_HOOK,) if featuretoggles.PLAN_REQUIRED else ()),
    *((REQUEST_REVIEW_HOOK,) if featuretoggles.REQUEST_REVIEW else ()),
    *((DIARY_READ_HOOK,) if featuretoggles.DAY_READ_REQUIRED else ()),
    *((AUTOAPPROVAL_HOOK,) if featuretoggles.AUTOAPPROVAL else ()),
)

# The log of changes is written by every feature that keeps an item, so it is Safwa's own
# view rather than one feature's.
OWN_VIEWS = (AI_LOG_EVENTS,)

REGISTRY: Registry = Registry.of(
    MODULES,
    world=_world,
    views=OWN_VIEWS,
    hooks=HOOKS,
    hook_policy=hook_switched_on,
)

# What the search index understands a text with. Both cut-offs are read off this model with
# scripts/search_probe.py, so another model is another pair of numbers (docs/SEARCH.md).
TEXT_MODEL = TextModel(
    # 384 numbers per text, about 0.22 GB, some 50 languages Russian among them.
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    # Low enough to list some near misses, such as mother and father, rather than miss a
    # duplicate.
    alike=0.70,
    related=0.50,
)
WORD_FORMS = SnowballWordForms({"cyrillic": "russian", "latin": "english"})

# What each part of the application reads off the registry, under the names it reads them
# by. The registry is one object; these are the views of it that are actually used.
AI_VIEWS = REGISTRY.views
ALLOWED_VIEWS = REGISTRY.allowed_views
# What can be opened and what can be cited are the same list, so the deep-link payload
# and both citation patterns are derived from it rather than written out again.
SCREENS = REGISTRY.screens
# The shell has commands of its own, so the composition root is what puts the two lists
# together; the order here is the order Telegram publishes them in.
FEATURE_COMMANDS = REGISTRY.commands
FEATURE_CALLBACK_ACTIONS = REGISTRY.callback_actions
FEATURE_TEXT_INPUTS = REGISTRY.text_inputs
FEATURE_START_LINKS = REGISTRY.start_links
PROPOSALS = REGISTRY.proposals
AGENTS = REGISTRY.agents
HELPERS = REGISTRY.helpers

# The routing rules are prose in the prompt, so a subagent they omit is never routed to,
# and so is a view the Advisor's own list leaves out.
SYSTEM_PROMPT: str = (
    SYSTEM_PROMPT_TEMPLATE.replace(
        "{routes}", REGISTRY.routes(lambda agent: f'- `route("{agent.name}")` — {agent.purpose}')
    )
    .replace("{views}", view_catalogue(AI_VIEWS, ADVISOR_VIEWS))
    .replace("{items}", ITEMS)
)


def routed_prompt(agent: AgentSpec) -> str:
    """What a routed subagent reads: the one persona block, then its own instructions, with
    the one wording of what each item is wherever they ask for `{items}`."""
    return f"{PERSONA}\n{agent.instructions}".replace("{items}", ITEMS)


def routed_subagents(context: AgentContext):
    """Bind every declared subagent to this application's read tools and clock."""
    return REGISTRY.subagents(context, prompt=routed_prompt)
