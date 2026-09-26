"""A photo the owner sent: kept once, labelled once, and read in words from then on.

The conversation never carries a photo. When one arrives it is looked at once, to write the
few words of its label, and the label is what every reader of the conversation gets from
then on: `[Анна с дочкой в парке](media:14)`. A reader that needs more asks a tool made for
one task — list this receipt, look again and answer this — and gets words back.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, LargeBinary, String, Text, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from llm_gateway import CompletionRequest, LlmProvider, ToolCall

from ..ai.contracts import ToolResultStatus
from ..ai.conversation import conversation_block
from ..ai.mini import ReadToolSpec
from ..foundation.models import Base, UtcDateTime

# How long a label is asked to be, and how many of the newest exchanges it is written from.
DESCRIPTION_MAX_WORDS = 5
DESCRIPTION_EXCHANGES = 3
# The longest side of the size that is kept. Telegram keeps several sizes of each photo, and
# a larger one costs a vision model more without reading any better.
PHOTO_MAX_SIDE = 1280
# What a photo is cited as: `[its label](media:14)`.
MEDIA_TYPE = "media"

DESCRIBE_PROMPT = f"""Write a label for the photo the user sent.
The label: at most {DESCRIPTION_MAX_WORDS} words, in the language of the caption and the conversation.
Say only what the photo shows.
The caption and the conversation give names: use them only for what you see in the photo.
"I", "me", "my" and "our" in the caption mean the user: write the user's name.
Answer with the label alone. No quotes, no full stop."""
# A label is a few words, and thinking first bought a local vision model nothing but time:
# 20 seconds and more a photo against under two, and the label no better.
DESCRIBE_REASONING = "none"

# What a label may not hold: it is the bracketed half of a citation, on one line.
_NOT_IN_A_LABEL = str.maketrans("", "", "[]()\"«»“”")


class ChatMedia(Base):
    """One photo the owner sent, in the size that is kept, under the label it was given."""

    __tablename__ = "chat_media"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meta: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(40))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    # Telegram's handle for the same file, so showing it again uploads nothing.
    file_id: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())


@dataclass(frozen=True, slots=True)
class Photo:
    """One photo as it arrived, in the size that is kept."""

    data: bytes
    width: int
    height: int
    file_id: str
    mime: str = "image/jpeg"


def media_label(media_id: int, meta: str) -> str:
    """What every reader of the conversation gets for a photo: a citation of it."""
    return f"[{meta}]({MEDIA_TYPE}:{media_id})"


def image_part(data: bytes, mime: str) -> dict[str, Any]:
    """A photo as a model is handed it: the file itself, in base64, inside the request.

    Never a link: a Telegram file link carries the bot's token, and a local model could not
    fetch it anyway.
    """
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


def last_exchanges(
    dialogue: Sequence[Mapping[str, Any]], count: int
) -> Sequence[Mapping[str, Any]]:
    """The newest `count` exchanges: each of the owner's turns, and what answered it."""
    starts = [index for index, message in enumerate(dialogue) if message.get("role") == "user"]
    return dialogue[starts[-count] :] if len(starts) >= count else dialogue


def _label_words(text: str) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return line.translate(_NOT_IN_A_LABEL).strip(" .'`") or "photo"


class MediaLibrary:
    """Where photos are kept, and the model call that looks at one."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], provider: LlmProvider) -> None:
        self.sessions = sessions
        self.provider = provider

    async def describe(
        self,
        photo: Photo,
        *,
        owner: str,
        caption: str,
        dialogue: Sequence[Mapping[str, Any]],
    ) -> str:
        """The words of a photo's label, written from the photo and what surrounds it."""
        context = [f"The user is {owner}."]
        conversation = conversation_block(last_exchanges(dialogue, DESCRIPTION_EXCHANGES))
        if conversation:
            context.append(f"The conversation before the photo, newest last:\n{conversation}")
        context.append(
            f"The user's caption: {caption}" if caption else "The photo has no caption."
        )
        words = await self._look(
            DESCRIBE_PROMPT,
            "\n\n".join(context),
            photo.data,
            photo.mime,
            reasoning_effort=DESCRIBE_REASONING,
        )
        return _label_words(words)

    async def keep(self, photos: Sequence[tuple[Photo, str]]) -> list[int]:
        """Keep each photo under its label's words, and return the numbers it is cited by."""
        async with self.sessions() as session:
            rows = [
                ChatMedia(
                    meta=meta,
                    mime=photo.mime,
                    data=photo.data,
                    width=photo.width,
                    height=photo.height,
                    file_id=photo.file_id,
                )
                for photo, meta in photos
            ]
            session.add_all(rows)
            await session.commit()
            return [row.id for row in rows]

    async def read(self, media: ChatMedia, instructions: str, question: str = "") -> str:
        """One photo read for one task, answered in words."""
        text = media_label(media.id, media.meta) + (f"\n{question}" if question else "")
        return await self._look(instructions, text, media.data, media.mime)

    async def _look(
        self,
        prompt: str,
        text: str,
        data: bytes,
        mime: str,
        *,
        reasoning_effort: str | None = None,
    ) -> str:
        turn = await self.provider.complete(
            CompletionRequest(
                messages=(
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": text}, image_part(data, mime)],
                    },
                ),
                reasoning_effort=reasoning_effort,
            )
        )
        return turn.content.strip()


def media_read_tool(
    library: MediaLibrary, *, name: str, description: str, instructions: str
) -> ReadToolSpec:
    """A tool that reads one photo for one task and answers in words.

    `instructions` is the whole task — list a receipt's lines, answer a question about what
    the photo shows. The words are what the session gets: the photo never enters its
    transcript.
    """
    schema: dict[str, Any] = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "media_id": {
                        "type": "integer",
                        "description": "N from the photo's label [words](media:N).",
                    },
                    "question": {
                        "type": "string",
                        "description": "What to find out, when the task leaves it open.",
                    },
                },
                "required": ["media_id"],
            },
        },
    }

    async def run(call: ToolCall) -> dict[str, Any]:
        try:
            arguments = json.loads(call.arguments_json or "{}")
            media_id = int(arguments["media_id"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            media_id = None
        async with library.sessions() as session:
            media = await session.get(ChatMedia, media_id) if media_id is not None else None
        if media is None:
            return {
                "status": ToolResultStatus.ERROR.value,
                "code": "media_not_found",
                "error": "No photo has that number.",
                "hint": f'Retry {name} with {{"media_id": N}}, N from a [words](media:N) label.',
                "retryable": True,
            }
        question = str(arguments.get("question") or "")
        return {"media_id": media.id, "text": await library.read(media, instructions, question)}

    return ReadToolSpec(schema, run)


RELOOK_INSTRUCTIONS = """Answer the question about the photo the user sent.
Say only what the photo shows. When it does not show the answer, say so.
With no question, say what the photo shows, in detail.
Answer in the language of the question."""


def relook_tool(library: MediaLibrary) -> ReadToolSpec:
    """The root session's second look at a photo, which its label is too short to answer."""
    return media_read_tool(
        library,
        name="relook",
        description="Look at a photo again, to answer the user's question about what it shows.",
        instructions=RELOOK_INSTRUCTIONS,
    )
