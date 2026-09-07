"""The screens the owner works with: the home list, one wallet, and the two editors.

Everything written here goes through `use_cases`, which is the same module an approved
proposal would call — the manual path and the AI path share their operations.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError
from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.telegram import (
    CallbackContext,
    CallbackHandler,
    Services,
    TextInputScreen,
    TextValidator,
    edit_registered_message,
    menu_row,
    render_text_input,
    required_text,
    send_registered,
    token_button,
)
from tg_agent_shell.telegram.contributions import TextInputFlow

from ..ledger.model import Entry
from ..ledger.telegram import entry_line, money
from .model import Category, CategoryKind, Wallet
from .use_cases import create_category, create_wallet, rename_wallet, wallet_balance

# How many of a wallet's entries its screen shows before the owner has to ask the model.
WALLET_ENTRY_LIMIT = 10


async def render_home(
    message: Message, services: Services, *, replace_message_id: int | None = None
) -> None:
    """Every wallet with what is in it, which is this application's home screen."""
    async with services.sessions() as session:
        wallets = list(await session.scalars(select(Wallet).order_by(Wallet.name)))
        lines = [
            f"{wallet.name}: {money(await wallet_balance(session, wallet.id), wallet.currency)}"
            for wallet in wallets
        ]
        rows = [
            [
                await token_button(
                    session, services.owner_id, wallet.name, "wallet_view", {"id": wallet.id}
                )
                for wallet in wallets[index : index + 2]
            ]
            for index in range(0, len(wallets), 2)
        ]
        rows.append(
            [
                await token_button(
                    session, services.owner_id, "➕ Wallet", "wallet_add_prompt", {}
                ),
                await token_button(
                    session, services.owner_id, "🏷 Categories", "category_list", {}
                ),
            ]
        )
        await session.commit()
    body = "<b>Wallet</b>\nWhat you have, and where it went. Write to me to add an entry."
    if lines:
        body += "\n\n" + html.escape("\n".join(lines))
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if replace_message_id is not None:
        await edit_registered_message(
            message, services, replace_message_id, body, kind=MessageKind.DASHBOARD, markup=markup
        )
        return
    await send_registered(message, services, body, kind=MessageKind.DASHBOARD, markup=markup)


async def render_wallet(
    message: Message, services: Services, wallet_id: int, *, replace: bool | None = None
) -> None:
    async with services.sessions() as session:
        wallet = await session.get(Wallet, wallet_id)
        if wallet is None:
            raise DomainError("Wallet does not exist")
        balance = await wallet_balance(session, wallet.id)
        entries = list(
            await session.scalars(
                select(Entry)
                .where(Entry.wallet_id == wallet.id)
                .order_by(Entry.happened_on.desc(), Entry.id.desc())
                .limit(WALLET_ENTRY_LIMIT)
            )
        )
        lines = [await entry_line(session, entry) for entry in entries]
        rename = await token_button(
            session, services.owner_id, "✏️ Rename", "wallet_rename_prompt", {"id": wallet.id}
        )
        await session.commit()
    body = f"<b>👛 {html.escape(wallet.name)}</b>\n{money(balance, wallet.currency)}"
    body += "\n\n" + html.escape("\n".join(lines) if lines else "Nothing here yet.")
    await send_registered(
        message,
        services,
        body,
        kind=MessageKind.DASHBOARD,
        markup=InlineKeyboardMarkup(inline_keyboard=[[rename], menu_row()]),
        related_id=wallet.id,
        replace=replace,
    )


async def open_wallet(
    message: Any, services: Any, item_id: int, *, replace: bool | None = None
) -> None:
    await render_wallet(message, services, item_id, replace=replace)


async def wallet_citation_label(session: AsyncSession, services: Any, wallet: Wallet) -> str:
    return f"{wallet.name} ({wallet.currency})"


async def render_categories(
    message: Message, services: Services, *, replace_message_id: int | None = None
) -> None:
    async with services.sessions() as session:
        categories = list(await session.scalars(select(Category).order_by(Category.name)))
        add = await token_button(
            session, services.owner_id, "➕ Category", "category_add_prompt", {}
        )
        await session.commit()
    listed = "\n".join(f"{item.name} — {item.kind}" for item in categories) or "None yet."
    body = "<b>Categories</b>\nWhat an entry is for, and which way the money goes.\n\n"
    body += html.escape(listed)
    markup = InlineKeyboardMarkup(inline_keyboard=[[add], menu_row()])
    if replace_message_id is not None:
        await edit_registered_message(
            message, services, replace_message_id, body, kind=MessageKind.DASHBOARD, markup=markup
        )
        return
    await send_registered(message, services, body, kind=MessageKind.DASHBOARD, markup=markup)


# ------------------------------------------------------------------ the typed value

_PROMPTS = {
    "wallet_new": ("New Wallet", "Send a name and a currency, such as: Cash USD"),
    "wallet_name": ("Rename Wallet", "Send the new name."),
    "category_new": ("New Category", "Send a name and income or expense, such as: Rent expense"),
}


async def _ask_for(
    message: Message, services: Services, *, field: str, item_id: int | None, current: str = ""
) -> None:
    title, instruction = _PROMPTS[field]
    await render_text_input(
        message,
        services,
        screen=TextInputScreen(
            title=title,
            current_value=current,
            instruction=instruction,
            back_action="wallet_home",
            back_payload={},
            related_id=item_id,
        ),
        state={"flow": "wallet", "field": field, "item_id": item_id},
    )


def _validator(state: Mapping[str, Any]) -> TextValidator[str]:
    return required_text(_PROMPTS[str(state["field"])][0])


async def _apply_text(
    session: AsyncSession, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del services
    field = str(state["field"])
    if field == "wallet_name":
        await rename_wallet(session, int(state["item_id"]), value)
        return
    name, _, tail = value.rpartition(" ")
    if not name:
        raise DomainError(_PROMPTS[field][1])
    if field == "wallet_new":
        await create_wallet(session, name=name, currency=tail)
        return
    if tail.strip().lower() not in {kind.value for kind in CategoryKind}:
        raise DomainError("The last word is income or expense")
    await create_category(session, name=name, kind=CategoryKind(tail.strip().lower()))


async def _render_after_text(
    message: Any, services: Any, state: Mapping[str, Any], value: str
) -> None:
    del value
    editor_message_id = int(state["text_input"]["message_id"])
    if str(state["field"]) == "category_new":
        await render_categories(message, services, replace_message_id=editor_message_id)
        return
    await render_home(message, services, replace_message_id=editor_message_id)


TEXT_INPUT = TextInputFlow(
    name="wallet", validator=_validator, apply=_apply_text, render=_render_after_text
)


# ------------------------------------------------------------------ the buttons


async def _on_view(context: CallbackContext) -> None:
    await render_wallet(context.message, context.services, context.payload["id"])


async def _on_add_prompt(context: CallbackContext) -> None:
    await _ask_for(context.message, context.services, field="wallet_new", item_id=None)


async def _on_rename_prompt(context: CallbackContext) -> None:
    wallet_id = int(context.payload["id"])
    async with context.sessions() as session:
        wallet = await session.get(Wallet, wallet_id)
        if wallet is None:
            raise DomainError("Wallet does not exist")
        current = wallet.name
    await _ask_for(
        context.message,
        context.services,
        field="wallet_name",
        item_id=wallet_id,
        current=current,
    )


async def _on_category_list(context: CallbackContext) -> None:
    await render_categories(context.message, context.services)


async def _on_category_add_prompt(context: CallbackContext) -> None:
    await _ask_for(context.message, context.services, field="category_new", item_id=None)


async def _on_home(context: CallbackContext) -> None:
    await render_home(context.message, context.services)


WALLET_CALLBACK_ACTIONS: dict[str, CallbackHandler] = {
    "wallet_view": _on_view,
    "wallet_add_prompt": _on_add_prompt,
    "wallet_rename_prompt": _on_rename_prompt,
    "wallet_home": _on_home,
    "category_list": _on_category_list,
    "category_add_prompt": _on_category_add_prompt,
}
