from __future__ import annotations

import pytest
from aiogram import Router

from tg_agent_shell.registry import Registry
from tg_agent_shell.telegram.commands import SHELL_COMMANDS, register_commands
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import AgentSpec, FeatureModule


async def _screen(*args):
    pass


async def _world(session):
    raise AssertionError("Registration must not read the database")


HOME = FeatureModule("home", commands=(ScreenCommand(_screen, command="start", nav="home"),))
AGENT = AgentSpec("writer", purpose="Write.", instructions="Write.")


@pytest.mark.parametrize("modules, name", [
    ((HOME, FeatureModule("home")), "home"),
    ((HOME, FeatureModule("one", agents=(AGENT, AGENT))), "writer"),
    ((HOME, FeatureModule("one", agents=(AGENT,)), FeatureModule("two", agents=(AGENT,))), "writer"),
    ((HOME, FeatureModule("one", commands=(ScreenCommand(_screen, command="start"),))), "start"),
    ((HOME, FeatureModule("one", commands=(ScreenCommand(_screen, nav="other"), ScreenCommand(_screen, nav="other")))), "other"),
])
def test_registration_refuses_duplicate_names(modules, name):
    with pytest.raises(RuntimeError, match=name):
        Registry.of(modules, world=_world)


def test_an_application_cannot_replace_the_shells_cancel_command():
    router = Router()
    with pytest.raises(RuntimeError, match="cancel"):
        register_commands(router, (*SHELL_COMMANDS, ScreenCommand(_screen, command="cancel")))
    assert not router.message.handlers
