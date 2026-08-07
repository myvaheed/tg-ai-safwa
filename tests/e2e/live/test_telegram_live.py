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

from safwa import main as safwa_main
from safwa.db import upgrade_database
from safwa.history import TelegramHistorySource
from safwa.qa import resolve_qa_config

pytestmark = [pytest.mark.e2e, pytest.mark.live_telegram]


class NoAIProvider:
    """The Telegram UI test must never make a provider or LM Studio request."""

    calls = 0

    def __init__(self, _config) -> None:
        self.closed = False

    async def complete(self, *_args, **_kwargs) -> str:
        self.__class__.calls += 1
        raise AssertionError("The live Telegram UI test attempted an unexpected AI request")

    async def close(self) -> None:
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


async def idle_background_job(*_args, **_kwargs) -> None:
    await asyncio.Event().wait()


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

    class SharedHistoryFactory:
        @classmethod
        def from_settings(cls, _settings, sessions, *, bot_user_id: int):
            return TelegramHistorySource(
                client,
                sessions,
                bot_user_id=bot_user_id,
                owner_id=settings.telegram_owner_id,
            )

    monkeypatch.setattr(safwa_main, "TelegramHistorySource", SharedHistoryFactory)
    NoAIProvider.calls = 0
    monkeypatch.setattr(safwa_main, "OpenAICompatibleProvider", NoAIProvider)
    monkeypatch.setattr(safwa_main, "run_scheduler", idle_background_job)
    monkeypatch.setattr(safwa_main, "run_memory_maintenance", idle_background_job)
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

        settings_command = await qa.send("/settings")
        settings = await qa.wait_for_bot(
            settings_command.id,
            lambda message: "Settings" in message.raw_text and has_button(message, "Menu"),
        )
        await click_button(settings, "Menu")
        home = await qa.wait_for_existing_bot_message(
            settings.id,
            lambda message: "Safwa" in message.raw_text and has_button(message, "Today"),
        )
        assert home.id == settings.id

        add_command = await qa.send("/add")
        review = await qa.wait_for_bot(
            add_command.id,
            lambda message: has_button(message, "Title") and has_button(message, "Effort"),
        )
        await click_button(review, "Title")
        await qa.wait_for_bot(
            review.id,
            lambda message: "Send the new title" in message.raw_text,
        )

        title_message = await qa.send(title)
        titled_review = await qa.wait_for_bot(
            title_message.id,
            lambda message: title in message.raw_text and has_button(message, "Effort"),
        )
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
            lambda message: has_button(message, "2", exact=True),
        )
        assert effort_prompt.id == titled_review.id
        await click_button(effort_prompt, "2", exact=True)
        ready_review = await qa.wait_for_existing_bot_message(
            effort_prompt.id,
            lambda message: title in message.raw_text and has_button(message, "Create"),
        )
        assert ready_review.id == titled_review.id
        await click_button(ready_review, "Create")
        card_detail = await qa.wait_for_existing_bot_message(
            ready_review.id,
            lambda message: title in message.raw_text and not has_button(message, "Create"),
        )
        assert card_detail.id == titled_review.id
        assert title in card_detail.raw_text
        await asyncio.sleep(0.5)
        assert not await qa.bot_messages_after(titled_review.id)

        with sqlite3.connect(qa.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM cards WHERE title=?", (title,)
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(*) FROM card_drafts WHERE title=? AND status='committed'", (title,)
            ).fetchone() == (1,)
    finally:
        await qa.delete_test_messages()
