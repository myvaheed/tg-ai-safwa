"""Every button, command and flow a feature declares is one the shell can reach.

These read the source of the adapters rather than any one screen: what they check is
that `MODULES` and `shell/` still add up, which no single feature can answer for.
"""

from __future__ import annotations

import ast

from aiogram import Dispatcher, Router
from aiogram.filters import Command
from ui_harness import (
    CALLBACK_ACTIONS,
    FakeMessage,
    services_for,
    ui_sources,
)

from safwa.bootstrap.modules import (
    FEATURE_COMMANDS,
    FEATURE_TEXT_INPUTS,
    SCREENS,
)
from safwa.features.home.api import MENU_LAYOUT, menu_markup
from tg_agent_shell.ai.contracts import open_tool
from tg_agent_shell.telegram import (
    SHELL_COMMANDS,
    OwnerAndWritingMiddleware,
    register_commands,
)
from tg_agent_shell.telegram.layout import menu_row, start_payload
from tg_agent_shell.telegram.routing import build_router


def _telegram_module_trees() -> list[ast.Module]:
    return [ast.parse(path.read_text(encoding="utf-8")) for path in ui_sources()]


def _callback_action_groups(trees: list[ast.Module]) -> list[ast.AST]:
    """Every per-feature group of inline-button actions, as the source declares them."""
    groups = [
        node
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id.endswith("_CALLBACK_ACTIONS")
        and isinstance(node.value, ast.Dict)
    ]
    if not groups:
        raise AssertionError("no callback action group was found in any telegram submodule")
    return groups


def test_every_inline_button_action_has_a_registered_handler() -> None:
    """An inline button whose action is unregistered is a screen that does nothing."""
    emitted = {
        node.args[3].value
        for tree in _telegram_module_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "token_button"
        and len(node.args) > 3
        and isinstance(node.args[3], ast.Constant)
        and isinstance(node.args[3].value, str)
    }

    assert emitted, "no literal token_button actions were found to check"
    assert emitted <= set(CALLBACK_ACTIONS)


def test_no_individually_registered_handler_is_unreachable() -> None:
    """A handler no button can reach is dead code, like the removed value_toggle."""
    trees = _telegram_module_trees()
    groups = _callback_action_groups(trees)
    registry_nodes = {id(node) for group in groups for node in ast.walk(group)}
    referenced = {
        node.value
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in registry_nodes
    }
    # Only keys spelled out in the registry are checked here; the generated selector
    # families build their names from the same constants the buttons use.
    spelled_out = {
        key.value
        for group in groups
        for key in group.value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }

    assert spelled_out, "no literal registry keys were found to check"
    assert spelled_out <= referenced


def test_every_recorded_text_input_flow_has_a_declared_handler() -> None:
    """A flow no feature declares is an editor that swallows what the owner types."""
    recorded: set[str] = set()
    for tree in _telegram_module_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                recorded |= {
                    value.value
                    for key, value in zip(node.keys, node.values, strict=True)
                    if isinstance(key, ast.Constant)
                    and key.value == "flow"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                }
            elif (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                recorded |= {
                    node.value.value
                    for target in node.targets
                    if isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "flow"
                }

    assert recorded, "no literal text input flow was found to check"
    assert recorded <= set(FEATURE_TEXT_INPUTS)


async def test_every_command_is_deleted_and_still_dispatched(sessions, monkeypatch) -> None:
    import tg_agent_shell.telegram.services as core_module

    monkeypatch.setattr(core_module, "Message", FakeMessage)
    middleware = OwnerAndWritingMiddleware()
    services = services_for(sessions)
    handled: list[str] = []

    async def handler(event, _data):
        handled.append(event.text)

    for index, command in enumerate(("/start", "/mem remember this", "/cancel"), start=1):
        message = FakeMessage(index, text=command, bot_message=False)
        await middleware(handler, message, {"services": services})
        assert message.was_deleted is True

    assert handled == ["/start", "/mem remember this", "/cancel"]


def test_every_declared_command_is_bound_to_its_command_line() -> None:
    """Binding left import time with the catalogue, so something has to check it."""
    commands = (*SHELL_COMMANDS, *FEATURE_COMMANDS)
    target = Router(name="test-commands")
    register_commands(target, commands)
    bound = {
        argument
        for handler in target.message.handlers
        for filter_ in handler.filters or ()
        if isinstance(filter_.callback, Command)
        for argument in filter_.callback.commands
    }

    assert bound == {screen.command for screen in commands if screen.command is not None}


def test_two_applications_each_own_their_router() -> None:
    """A router is built per application, so a second one starts with the same handlers."""
    commands = (*SHELL_COMMANDS, *FEATURE_COMMANDS)
    first = build_router(commands)
    second = build_router(commands)

    Dispatcher().include_router(first)
    Dispatcher().include_router(second)

    assert first is not second
    assert len(first.message.handlers) == len(second.message.handlers)
    assert len(first.callback_query.handlers) == len(second.callback_query.handlers)


def test_the_menu_draws_every_label_a_screen_declared() -> None:
    """HM-MENU-001 — tests/brd/home.feature"""
    # The titles are the features', the layout is Home's, and a title the layout does not
    # name is dropped without a word.
    commands = (*FEATURE_COMMANDS, *SHELL_COMMANDS)
    placed = [nav for row in MENU_LAYOUT for nav in row]
    assert len(placed) == len(set(placed))
    assert sorted(placed) == sorted(
        screen.nav for screen in commands if screen.title is not None
    )

    rows = menu_markup(commands).inline_keyboard
    drawn = [button.callback_data.split(":", 1)[1] for row in rows for button in row]
    assert drawn == placed
    # Home is the one action reached without a menu button of its own.
    assert menu_row()[0].callback_data == "nav:home"
    assert {screen.nav for screen in commands if screen.nav is not None} == {
        *drawn,
        "home",
    }


def test_pl_mode_001_today_stays_on_the_menu_and_among_the_commands_in_planning() -> None:
    """PL-MODE-001 — tests/brd/planning.feature"""
    # One menu and one command list, whatever mode the workspace is in: Today opened in
    # Planning is the screen that says no Sprint runs.
    drawn = [
        button.callback_data
        for row in menu_markup(FEATURE_COMMANDS).inline_keyboard
        for button in row
    ]

    assert "nav:today" in drawn
    assert "today" in {screen.command for screen in FEATURE_COMMANDS}
    assert "nav:sprint" in drawn


def test_the_screen_catalogue_is_the_one_list_of_openable_items() -> None:
    """RT-OPEN-002 — tests/brd/retro.feature"""
    # One catalogue answers both questions, so what can be cited and what `open` may name
    # cannot drift apart: the retro is in it, and so is every other published screen.
    assert set(SCREENS.types) == {"card", "check", "tag", "value", "request", "diary", "retro"}
    enum = open_tool(SCREENS)["function"]["parameters"]["properties"]["item_type"]["enum"]
    assert set(enum) == set(SCREENS.types)


def test_start_payload_reads_only_a_command_line() -> None:
    assert start_payload("/start card-12") == "card-12"
    assert start_payload("/start@safwa_ai_bot card-12") == "card-12"
    assert start_payload("/start") is None
    # The menu's Home button hands `render_home` the bot's own screen, never a command.
    assert start_payload("<b>Card</b>: Pull ups") is None
    assert SCREENS.parse_payload("check-14") == ("check", 14)
    assert SCREENS.parse_payload("sprint-1") is None
