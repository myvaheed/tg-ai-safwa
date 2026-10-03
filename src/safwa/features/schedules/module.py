"""Schedules: the one compiled plan for Cards and Checks."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule

from .agent import ScheduleCompiler as ScheduleCompiler
from .agent import scheduled_tool as scheduled_tool
from .hooks import SCHEDULE_CLARIFICATION_HOOK as SCHEDULE_CLARIFICATION_HOOK
from .hooks import SCHEDULE_RECOVERY_HOOK as SCHEDULE_RECOVERY_HOOK
from .hooks import SCHEDULER_HOOK as SCHEDULER_HOOK

MODULE = FeatureModule(name="schedules")
