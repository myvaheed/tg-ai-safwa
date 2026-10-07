"""Back, as the shell builds it: a screen goes back to where it was opened from.

The way back travels in the button that leads to a screen, so these drive the example
application's own screens. Nothing here imports Safwa.
"""

from __future__ import annotations

import re

from telegram_fakes import QueueTestMessage
from wallet_harness import press, seed_lists

from tg_agent_shell.telegram import Place, claimed_link, open_home, place_link
from tg_agent_shell.telegram.place import NAV_DEPTH


def test_a_place_is_what_its_button_carries() -> None:
    """SC-BACK-012 — tests/brd/tg_agent_shell/screens.feature"""
    list_page = Place("item_list", {"page": 2})
    item = list_page.child("item_view", id=7)

    assert Place.of(item.action, item.payload) == item
    assert Place.at(item.address) == item
    assert item.back == list_page
    # Another state of the same screen keeps the way back it had.
    assert item.but(full=True) == Place("item_view", {"id": 7, "full": True}, list_page)


def test_the_way_back_forgets_what_is_deeper_than_its_depth() -> None:
    """SC-BACK-012 — tests/brd/tg_agent_shell/screens.feature"""
    place = Place("screen_0")
    for index in range(1, NAV_DEPTH + 5):
        place = place.child(f"screen_{index}")

    kept = Place.of(place.action, place.payload)
    chain = []
    while kept is not None:
        chain.append(kept.action)
        kept = kept.back
    assert len(chain) == NAV_DEPTH
    assert chain[0] == place.action


async def test_back_returns_to_the_screen_the_item_was_opened_from(wallet_bot) -> None:
    """SC-BACK-012 — tests/brd/tg_agent_shell/screens.feature"""
    running = await wallet_bot.start()
    await seed_lists(running.sessions)
    await open_home(running.message, running.services)

    await press(running, "wallet_view")
    assert "👛" in running.chat[-1]
    assert running.screen.buttons()[-1] == "↩️ Back"

    await press(running, "wallet_home")
    assert "<b>Wallet</b>" in running.chat[-1]


async def test_an_item_opened_from_a_link_offers_the_menu(wallet_bot) -> None:
    """SC-BACK-012 — tests/brd/tg_agent_shell/screens.feature"""
    running = await wallet_bot.start()
    ids = await seed_lists(running.sessions)

    await running.services.screens.by_type["wallet"].open(
        running.message, running.services, ids["cash"]
    )

    assert running.screen.buttons()[-1] == "↩️ Menu"


async def test_a_link_inside_a_screen_opens_in_its_place(wallet_bot) -> None:
    """SC-LINK-013 — tests/brd/tg_agent_shell/screens.feature"""
    running = await wallet_bot.start()
    ids = await seed_lists(running.sessions)
    running.services.bot_username = "wallet_bot"
    await open_home(running.message, running.services)
    home = running.screen

    async with running.sessions() as session:
        link = await place_link(
            session,
            running.services,
            "Cash",
            Place("wallet_home").child("wallet_view", id=ids["cash"]),
        )
        await session.commit()
    payload = re.search(r"\?start=([\w-]+)", link)[1]
    tap = QueueTestMessage(
        message_id=home.message_id + 1, is_bot=False, text=f"/start {payload}", parent=home
    )
    await claimed_link(running.services, payload).open(tap, running.services, payload)

    # The wallet took the screen's own message rather than arriving as one of its own.
    edited_id, markup = home.bot.edited[-1]
    assert edited_id == home.message_id
    assert "👛 Cash" in running.chat[-1]
    assert [button.text for row in markup.inline_keyboard for button in row][-1] == "↩️ Back"

    # A link works once, like any button.
    await claimed_link(running.services, payload).open(tap, running.services, payload)
    assert "out of date" in running.chat[-1]
