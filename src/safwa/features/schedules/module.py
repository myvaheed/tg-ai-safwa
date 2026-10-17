"""Schedules: the one compiled plan for Cards and Checks."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule

from .agent import ScheduleCompiler as ScheduleCompiler
from .agent import scheduled_tool as scheduled_tool
from .hooks import SCHEDULE_TOTAL_HOOK as SCHEDULE_TOTAL_HOOK
from .telegram import SCHEDULE_CALLBACK_ACTIONS

MODULE = FeatureModule(name="schedules", callback_actions=SCHEDULE_CALLBACK_ACTIONS)
