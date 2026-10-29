from __future__ import annotations

import pytest
from aiogram import Router

from tg_agent_shell.ai.sql import SqlView
from tg_agent_shell.registry import Registry
from tg_agent_shell.telegram.commands import SHELL_COMMANDS, register_commands
from tg_agent_shell.telegram.contributions import ScreenCommand, TextInputFlow
from tg_agent_shell.telegram.manifest import AgentSpec, FeatureModule


async def _screen(*args):
    pass


async def _world(session):
    raise AssertionError("Registration must not read the database")


HOME = FeatureModule("home", commands=(ScreenCommand(_screen, command="start", nav="home"),))
AGENT = AgentSpec("writer", purpose="Write.", instructions="Write.")
VIEW = SqlView("ai_entries", "SELECT 1 AS id")
INPUT = TextInputFlow("entry_note", validator=lambda _: None, apply=_screen, render=_screen)


@pytest.mark.parametrize("modules, name", [
    ((HOME, FeatureModule("home")), "home"),
    ((HOME, FeatureModule("one", agents=(AGENT, AGENT))), "writer"),
    ((HOME, FeatureModule("one", agents=(AGENT,)), FeatureModule("two", agents=(AGENT,))), "writer"),
    ((HOME, FeatureModule("one", commands=(ScreenCommand(_screen, command="start"),))), "start"),
    ((HOME, FeatureModule("one", commands=(ScreenCommand(_screen, nav="other"), ScreenCommand(_screen, nav="other")))), "other"),
    ((HOME, FeatureModule("one", views=(VIEW,)), FeatureModule("two", views=(VIEW,))), "ai_entries"),
    ((HOME, FeatureModule("one", callback_actions={"entry_note": _screen}), FeatureModule("two", callback_actions={"entry_note": _screen})), "entry_note"),
    ((HOME, FeatureModule("one", text_inputs=(INPUT,)), FeatureModule("two", text_inputs=(INPUT,))), "entry_note"),
])
def test_registration_refuses_duplicate_names(modules, name):
    with pytest.raises(RuntimeError, match=name):
        Registry.of(modules, world=_world)


def test_an_application_cannot_replace_the_shells_cancel_command():
    router = Router()
    with pytest.raises(RuntimeError, match="cancel"):
        register_commands(router, (*SHELL_COMMANDS, ScreenCommand(_screen, command="cancel")))
    assert not router.message.handlers
