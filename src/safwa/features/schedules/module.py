"""Schedules: the one compiled plan for Cards and Checks."""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import FeatureModule

from .agent import ScheduleCompiler as ScheduleCompiler
from .agent import scheduled_tool as scheduled_tool
from .hooks import SCHEDULE_END_HOOK as SCHEDULE_END_HOOK
from .hooks import SCHEDULE_PERIOD_HOOK as SCHEDULE_PERIOD_HOOK
from .telegram import SCHEDULE_CALLBACK_ACTIONS, SCHEDULE_TEXT_INPUTS

MODULE = FeatureModule(
    name="schedules",
    callback_actions=SCHEDULE_CALLBACK_ACTIONS,
    text_inputs=SCHEDULE_TEXT_INPUTS,
)
