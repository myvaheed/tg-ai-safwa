"""The Telethon user session: the one place that turns settings into a client.

The bot cannot read its own chat, so the history reader signs in as the owner. Building
that client needs three settings, which is why it is here rather than in the adapter.
"""

from __future__ import annotations

import asyncio
import getpass
from pathlib import Path

from telethon import TelegramClient

from ..config import Settings


def history_client(settings: Settings) -> TelegramClient:
    return TelegramClient(
        str(settings.telegram_user_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),  # type: ignore[union-attr]
    )


def auth_main() -> None:
    settings = Settings()
    if not settings.telegram_history_enabled:
        raise SystemExit("Set SAFWA_TELEGRAM_API_ID and SAFWA_TELEGRAM_API_HASH first")

    async def authenticate() -> None:
        Path(settings.telegram_user_session_path).parent.mkdir(parents=True, exist_ok=True)
        client = history_client(settings)
        await client.start(
            phone=lambda: input("Telegram phone: "),
            code_callback=lambda: input("Telegram code: "),
            password=lambda: getpass.getpass("2FA password: "),
        )
        await client.disconnect()

    asyncio.run(authenticate())


if __name__ == "__main__":
    auth_main()
