"""Safwa's Telegram application: the container, the chat border, the shared screens.

This is what a feature's Telegram adapter is allowed to import besides `telegram_llm`.
Nothing here belongs to one feature: the router and the middleware, the verbs that put a
message in the chat under a `MessageKind`, the layout a screen is drawn in, the editor that
takes one typed value, and the dispatch that opens a cited item.
"""

from __future__ import annotations

from .chat import (
    TURN_NOTICE,
    delete_screen,
    delete_text_input,
    discard_stale_status,
    discard_toast,
    dismiss_prior_ui,
    edit_registered_message,
    end_turn,
    open_turn_notice,
    owner_display_name,
    paging_row,
    remove_turn_notice,
    send_owner_turn,
    send_prose,
    send_registered,
    send_summary,
    send_toast,
    token_button,
)
from .layout import (
    Page,
    menu_markup,
    menu_row,
    paginate,
    short_citation_title,
    start_payload,
    with_citation_fields,
    with_notice,
)
from .screens import (
    open_citation,
    open_item_screen,
    render_citations,
    report_open_failure,
)
from .services import (
    CallbackContext,
    CallbackHandler,
    OwnerAndWritingMiddleware,
    Services,
    audio_payload,
    router,
    sprint_is_active,
)
from .text_input import (
    TextInputAction,
    TextInputScreen,
    TextValidator,
    handle_text_input,
    reject_text_input,
    render_text_input,
    required_text,
    rerender_text_input,
    validate_text_input,
)

__all__ = [
    "TURN_NOTICE",
    "CallbackContext",
    "CallbackHandler",
    "OwnerAndWritingMiddleware",
    "Page",
    "Services",
    "TextInputAction",
    "TextInputScreen",
    "TextValidator",
    "audio_payload",
    "delete_screen",
    "delete_text_input",
    "discard_stale_status",
    "discard_toast",
    "dismiss_prior_ui",
    "edit_registered_message",
    "end_turn",
    "handle_text_input",
    "menu_markup",
    "menu_row",
    "open_citation",
    "open_item_screen",
    "open_turn_notice",
    "owner_display_name",
    "paginate",
    "paging_row",
    "reject_text_input",
    "remove_turn_notice",
    "render_citations",
    "render_text_input",
    "report_open_failure",
    "required_text",
    "rerender_text_input",
    "router",
    "send_owner_turn",
    "send_prose",
    "send_registered",
    "send_summary",
    "send_toast",
    "short_citation_title",
    "sprint_is_active",
    "start_payload",
    "token_button",
    "validate_text_input",
    "with_citation_fields",
    "with_notice",
]
