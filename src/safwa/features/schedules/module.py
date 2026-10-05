"""Schedules: the one compiled plan for Cards and Checks."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule

from .agent import ScheduleCompiler as ScheduleCompiler
from .agent import scheduled_tool as scheduled_tool
from .telegram import SCHEDULE_CALLBACK_ACTIONS

MODULE = FeatureModule(name="schedules", callback_actions=SCHEDULE_CALLBACK_ACTIONS)
