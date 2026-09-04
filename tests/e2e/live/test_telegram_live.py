from __future__ import annotations

import asyncio
import sqlite3
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest
import pytest_asyncio
from aiogram import Bot
from telethon import TelegramClient
from telethon.tl.custom.message import Message
from telethon.tl.types import User

from safwa.bootstrap import main as safwa_main
from safwa.foundation.database import upgrade_database
from safwa.qa import resolve_qa_config
from tg_agent_shell.history import TelegramHistorySource
from tg_agent_shell.telegram import router as safwa_router

pytestmark = [pytest.mark.e2e, pytest.mark.live_telegram]


class NoAIProvider:
    """The Telegram UI test must never make a provider or LM Studio request."""

    calls = 0

    def __init__(self, _config) -> None:
        self.closed = False

    async def complete(self, *_args, **_kwargs) -> str:
        self.__class__.calls += 1
        raise AssertionError("The live Telegram UI test attempted an unexpected AI request")

    async def aclose(self) -> None:
        self.closed = True


def has_button(message: Message, text: str, *, exact: bool = False) -> bool:
    expected = text.casefold()
    for row in message.buttons or []:
        for button in row:
            actual = button.text.casefold()
            if actual == expected if exact else expected in actual:
                return True
    return False


async def click_button(message: Message, text: str, *, exact: bool = False) -> None:
    expected = text.casefold()
    for row in message.buttons or []:
        for button in row:
            actual = button.text.casefold()
            if actual == expected if exact else expected in actual:
                await button.click()
                return
    labels = [button.text for row in message.buttons or [] for button in row]
    raise AssertionError(f"Button {text!r} was not found; available: {labels}")


@dataclass
class LiveTelegramHarness:
    client: TelegramClient
    bot_entity: User
    app_task: asyncio.Task
    database_path: Path
    timeout: float
    keep_messages: bool
    message_ids: set[int] = field(default_factory=set)

    async def send(self, text: str) -> Message:
        message = await self.client.send_message(self.bot_entity, text)
        self.message_ids.add(message.id)
        return message

    async def wait_for_bot(self, after_id: int, predicate) -> Message:
        deadline = monotonic() + self.timeout
        while monotonic() < deadline:
            if self.app_task.done():
                await self.app_task
                raise AssertionError("Safwa-QA stopped before replying")
            messages = await self.client.get_messages(
                self.bot_entity,
                limit=50,
                min_id=after_id,
            )
            for message in sorted(messages, key=lambda item: item.id):
                if message.sender_id == self.bot_entity.id and predicate(message):
                    self.message_ids.add(message.id)
                    return message
            await asyncio.sleep(0.5)
        raise AssertionError(f"Safwa-QA did not produce the expected reply within {self.timeout}s")

    async def wait_for_existing_bot_message(self, message_id: int, predicate) -> Message:
        """Wait for an inline callback to edit its originating bot message."""
        deadline = monotonic() + self.timeout
        while monotonic() < deadline:
            if self.app_task.done():
                await self.app_task
                raise AssertionError("Safwa-QA stopped before updating the UI")
            message = await self.client.get_messages(self.bot_entity, ids=message_id)
            if message and message.sender_id == self.bot_entity.id and predicate(message):
                self.message_ids.add(message.id)
                return message
            await asyncio.sleep(0.5)
        raise AssertionError(
            f"Safwa-QA did not update bot message {message_id} within {self.timeout}s"
        )

    async def bot_messages_after(self, message_id: int) -> list[Message]:
        messages = await self.client.get_messages(self.bot_entity, limit=50, min_id=message_id)
        return [message for message in messages if message.sender_id == self.bot_entity.id]

    async def delete_test_messages(self) -> None:
        if self.keep_messages or not self.message_ids or not self.client.is_connected():
            return
        message_ids = sorted(self.message_ids)
        self.message_ids.clear()
        with suppress(Exception):
            await self.client.delete_messages(self.bot_entity, message_ids, revoke=True)


@pytest_asyncio.fixture
async def live_telegram_harness(tmp_path: Path, monkeypatch) -> LiveTelegramHarness:
    repository_root = Path(__file__).parents[3]
    monkeypatch.chdir(repository_root)
    database_path = tmp_path / "safwa-qa-live.db"
    data_dir = tmp_path / "data"
    try:
        resolved = resolve_qa_config(
            data_dir=data_dir,
            database_url=f"sqlite:///{database_path.as_posix()}",
        )
    except (ValueError, OSError) as error:
        pytest.fail(f"Invalid Safwa-QA configuration: {error}")
    settings = resolved.settings
    upgrade_database(settings.database_url)

    probe = Bot(token=settings.telegram_bot_token.get_secret_value())
    try:
        bot_user = await probe.get_me()
    finally:
        await probe.session.close()
    if not bot_user.username:
        pytest.fail("Safwa-QA bot has no Telegram username")

    client = TelegramClient(
        str(settings.telegram_user_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),  # type: ignore[union-attr]
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        pytest.fail("Safwa-QA user session is not authorized; run `uv run safwa-qa-auth`")
    owner = await client.get_me()
    if owner.id != settings.telegram_owner_id:
        await client.disconnect()
        pytest.fail("Safwa-QA session user does not match SAFWA_QA_TELEGRAM_OWNER_ID")
    bot_entity = await client.get_entity(bot_user.username)

    def shared_history(_client, sessions, **kwargs):
        # The QA run already holds an authorized session, so the bot reuses it rather
        # than signing in a second one against the same account.
        return TelegramHistorySource(client, sessions, **kwargs)

    monkeypatch.setattr(safwa_main, "TelegramHistorySource", shared_history)
    NoAIProvider.calls = 0
    monkeypatch.setattr(safwa_main, "OpenAICompatibleProvider", NoAIProvider)
    monkeypatch.setattr(safwa_main, "BACKGROUND_TASKS", ())
    app_task = asyncio.create_task(safwa_main.run(settings), name="safwa-qa-live")
    harness = LiveTelegramHarness(
        client,
        bot_entity,
        app_task,
        database_path,
        resolved.live_timeout_seconds,
        resolved.keep_messages,
    )
    print(f"Safwa-QA live target: @{bot_user.username}; keep_messages={resolved.keep_messages}")
    try:
        yield harness
    finally:
        await harness.delete_test_messages()
        app_task.cancel()
        with suppress(asyncio.CancelledError):
            await app_task
        # Aiogram routers are singleton module objects in the application.  A
        # fresh live harness needs to attach it to its own Dispatcher.
        safwa_router._parent_router = None  # type: ignore[attr-defined]
        if client.is_connected():
            await client.disconnect()
        assert NoAIProvider.calls == 0


async def test_qa_status_and_manual_card_review_flow(live_telegram_harness):
    qa = live_telegram_harness
    title = f"QA push ups {uuid4().hex[:8]}"
    try:
        status_command = await qa.send("/status")
        status = await qa.wait_for_bot(
            status_command.id,
            lambda message: "Status" in message.raw_text and "Mode: planning" in message.raw_text,
        )
        assert "Memory: OK" in status.raw_text

        profile_command = await qa.send("/profile")
        profile = await qa.wait_for_bot(
            profile_command.id,
            lambda message: "Profile" in message.raw_text and has_button(message, "Menu"),
        )
        await click_button(profile, "Menu")
        home = await qa.wait_for_existing_bot_message(
            profile.id,
            lambda message: "Safwa" in message.raw_text and has_button(message, "Add"),
        )
        assert home.id == profile.id

        await click_button(home, "Add")
        review = await qa.wait_for_existing_bot_message(
            home.id,
            lambda message: has_button(message, "Title") and has_button(message, "Effort"),
        )
        await click_button(review, "Title")
        title_prompt = await qa.wait_for_existing_bot_message(
            review.id,
            lambda message: "Edit Card Title" in message.raw_text,
        )
        assert title_prompt.id == review.id

        await qa.send(title)
        titled_review = await qa.wait_for_existing_bot_message(
            review.id,
            lambda message: title in message.raw_text and has_button(message, "Effort"),
        )
        assert titled_review.id == review.id
        await click_button(titled_review, "Stage")
        stage_choices = await qa.wait_for_existing_bot_message(
            titled_review.id,
            lambda message: has_button(message, "Back") and has_button(message, "Today"),
        )
        await click_button(stage_choices, "Back")
        titled_review = await qa.wait_for_existing_bot_message(
            stage_choices.id,
            lambda message: title in message.raw_text and has_button(message, "Effort"),
        )
        await click_button(titled_review, "Effort")
        effort_prompt = await qa.wait_for_existing_bot_message(
            titled_review.id,
            lambda message: has_button(message, "2 EP", exact=True),
        )
        assert effort_prompt.id == titled_review.id
        await click_button(effort_prompt, "2 EP", exact=True)
        ready_review = await qa.wait_for_existing_bot_message(
            effort_prompt.id,
            lambda message: title in message.raw_text and has_button(message, "Save"),
        )
        assert ready_review.id == titled_review.id
        await click_button(ready_review, "Save")
        created = await qa.wait_for_existing_bot_message(
            ready_review.id,
            lambda message: title in message.raw_text and not has_button(message, "Save"),
        )
        assert created.id == titled_review.id
        assert title in created.raw_text

        with sqlite3.connect(qa.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM cards WHERE title=?", (title,)
            ).fetchone() == (1,)
    finally:
        await qa.delete_test_messages()


async def _wait_for_row(database_path: Path, sql: str, parameters: tuple, timeout: float) -> tuple:
    """Poll the QA database until a callback's transaction is visible."""
    deadline = monotonic() + timeout
    row: tuple = ()
    while monotonic() < deadline:
        with sqlite3.connect(database_path) as connection:
            row = connection.execute(sql, parameters).fetchone()
        if row and row[0] is not None:
            return row
        await asyncio.sleep(0.5)
    raise AssertionError(f"Row {parameters} never appeared within {timeout}s; last read {row}")


def _seed_linked_check(database_path: Path, card_title: str, check_title: str) -> None:
    """Hang one Pending Check on a Card straight in SQLite.

    Creating and linking a Check are AI proposals only, and this test refuses every
    provider call, so the fixture is written rather than clicked.
    """
    with sqlite3.connect(database_path) as connection:
        card_id = connection.execute(
            "SELECT id FROM cards WHERE title=?", (card_title,)
        ).fetchone()[0]
        cursor = connection.execute(
            "INSERT INTO checks (title, repeatable, version) VALUES (?, 0, 1)", (check_title,)
        )
        check_id = cursor.lastrowid
        connection.execute("UPDATE checks SET series_id=? WHERE id=?", (check_id, check_id))
        connection.execute(
            "INSERT INTO card_checks (card_id, check_id) VALUES (?, ?)", (card_id, check_id)
        )


async def test_qa_check_gate_blocks_done_until_every_check_is_answered(live_telegram_harness):
    qa = live_telegram_harness
    title = f"QA market {uuid4().hex[:8]}"
    check_title = f"QA milk {uuid4().hex[:8]}"
    try:
        add_command = await qa.send("/start")
        home = await qa.wait_for_bot(
            add_command.id,
            lambda message: "Safwa" in message.raw_text and has_button(message, "Add"),
        )
        await click_button(home, "Add")
        draft = await qa.wait_for_existing_bot_message(
            home.id,
            lambda message: has_button(message, "Title") and has_button(message, "Effort"),
        )
        await click_button(draft, "Title")
        await qa.wait_for_existing_bot_message(
            draft.id, lambda message: "Edit Card Title" in message.raw_text
        )
        await qa.send(title)
        titled = await qa.wait_for_existing_bot_message(
            draft.id,
            lambda message: title in message.raw_text and has_button(message, "Effort"),
        )
        await click_button(titled, "Effort")
        efforts = await qa.wait_for_existing_bot_message(
            titled.id, lambda message: has_button(message, "2 EP", exact=True)
        )
        await click_button(efforts, "2 EP", exact=True)
        ready = await qa.wait_for_existing_bot_message(
            efforts.id,
            lambda message: title in message.raw_text and has_button(message, "Save"),
        )
        await click_button(ready, "Save")
        # Saving a draft leaves a receipt, not the Card screen; reach the Card through
        # the Backlog dashboard, which is where a new Card lands by default.
        await qa.wait_for_existing_bot_message(
            ready.id,
            lambda message: "Created" in message.raw_text and title in message.raw_text,
        )
        _seed_linked_check(qa.database_path, title, check_title)

        backlog_command = await qa.send("/backlog")
        dashboard = await qa.wait_for_bot(
            backlog_command.id, lambda message: has_button(message, title)
        )
        await click_button(dashboard, title)
        card = await qa.wait_for_existing_bot_message(
            dashboard.id,
            lambda message: title in message.raw_text and has_button(message, "Checks"),
        )

        # The manual Check screen answers and repeats; it cannot rename, link or archive.
        await click_button(card, "Checks")
        checks = await qa.wait_for_existing_bot_message(
            card.id,
            lambda message: has_button(message, check_title)
            and not has_button(message, "Add Check"),
        )
        await click_button(checks, check_title)
        check_screen = await qa.wait_for_existing_bot_message(
            checks.id,
            lambda message: check_title in message.raw_text
            and has_button(message, "Repeat")
            and has_button(message, "Passed")
            and not has_button(message, "Title"),
        )
        await click_button(check_screen, "Back")
        listed = await qa.wait_for_existing_bot_message(
            check_screen.id,
            lambda message: has_button(message, check_title)
            and not has_button(message, "Passed"),
        )
        await click_button(listed, "Back")
        card = await qa.wait_for_existing_bot_message(
            listed.id,
            lambda message: title in message.raw_text and has_button(message, "Done"),
        )

        # Done must not finish the Card while a Check is unanswered.
        await click_button(card, "Done")
        gate = await qa.wait_for_existing_bot_message(
            card.id,
            lambda message: "Pending Checks" in message.raw_text
            and "— Pending" in message.raw_text
            # Save appears only once an answer is set.
            and not has_button(message, "Save"),
        )
        card_row = await _wait_for_row(
            qa.database_path,
            "SELECT effective_stage FROM cards WHERE title=?",
            (title,),
            qa.timeout,
        )
        assert card_row[0] == "backlog"

        await click_button(gate, f"✅ {check_title}")
        answered = await qa.wait_for_existing_bot_message(
            gate.id,
            lambda message: "Passed" in message.raw_text and has_button(message, "Save"),
        )
        await click_button(answered, "Save")

        stage_row = await _wait_for_row(
            qa.database_path,
            "SELECT effective_stage FROM cards WHERE title=? AND effective_stage='done'",
            (title,),
            qa.timeout,
        )
        assert stage_row[0] == "done"
        outcome_row = await _wait_for_row(
            qa.database_path,
            "SELECT outcome, resolved_at FROM checks WHERE title=?",
            (check_title,),
            qa.timeout,
        )
        assert outcome_row[0] == "passed"
        assert outcome_row[1] is not None
    finally:
        await qa.delete_test_messages()


async def test_qa_tags_and_requests_navigation(live_telegram_harness):
    qa = live_telegram_harness
    tag_name = f"QA family {uuid4().hex[:8]}"
    try:
        tags_command = await qa.send("/tags")
        tags = await qa.wait_for_bot(
            tags_command.id,
            lambda message: "Tags" in message.raw_text and has_button(message, "Add Tag"),
        )
        await click_button(tags, "Add Tag")
        editor = await qa.wait_for_existing_bot_message(
            tags.id,
            lambda message: (
                "Create Tag" in message.raw_text
                and has_button(message, "Name")
                and has_button(message, "Description")
            ),
        )
        assert editor.id == tags.id
        await click_button(editor, "Name")
        prompt = await qa.wait_for_existing_bot_message(
            editor.id,
            lambda message: "Edit Tag Name" in message.raw_text,
        )
        assert prompt.id == tags.id
        await qa.send(tag_name)
        ready = await qa.wait_for_existing_bot_message(
            editor.id,
            lambda message: tag_name in message.raw_text and has_button(message, "Create Tag"),
        )
        assert ready.id == tags.id
        await click_button(ready, "Create Tag")
        tag_detail = await qa.wait_for_existing_bot_message(
            tags.id,
            lambda message: (
                tag_name in message.raw_text
                and has_button(message, "Name")
                and has_button(message, "Back")
                and not has_button(message, "Create Tag")
            ),
        )
        assert tag_detail.id == tags.id
        await click_button(tag_detail, "Back")
        tags = await qa.wait_for_existing_bot_message(
            tag_detail.id,
            lambda message: "Tags" in message.raw_text and has_button(message, tag_name),
        )
        await click_button(tags, tag_name, exact=True)
        tag_detail = await qa.wait_for_existing_bot_message(
            tags.id,
            lambda message: tag_name in message.raw_text and has_button(message, "Back"),
        )
        assert tag_detail.id == tags.id

        requests_command = await qa.send("/requests")
        requests = await qa.wait_for_bot(
            requests_command.id,
            lambda message: "Requests" in message.raw_text and has_button(message, "Menu"),
        )
        assert "Saved card queries" in requests.raw_text
    finally:
        await qa.delete_test_messages()
