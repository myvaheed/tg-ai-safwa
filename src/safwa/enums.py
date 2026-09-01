from __future__ import annotations

from enum import StrEnum


class AIProvider(StrEnum):
    LMSTUDIO = "lmstudio"
    OPENROUTER = "openrouter"


class ASRProvider(StrEnum):
    """Where a voice message is transcribed.

    The first three speak the same OpenAI-compatible API; `faster_whisper` decodes in
    this process instead of calling one.
    """

    OFF = "off"
    OPENAI = "openai"
    GROQ = "groq"
    LOCAL = "local"
    FASTER_WHISPER = "faster_whisper"


class WorkspaceMode(StrEnum):
    PLANNING = "planning"
    SPRINT = "sprint"


class CardKind(StrEnum):
    GOAL = "goal"
    IDEA = "idea"
    ACTION = "action"


class Priority(StrEnum):
    CRITICAL = "critical"
    MEDIUM = "medium"
    LOW = "low"


class ScheduleKind(StrEnum):
    """How a Reminder repeats. Derived from the resolved parameters, never model-supplied."""

    ONCE = "once"
    INTERVAL = "interval"
    DAILY = "daily"
    WEEKLY = "weekly"


class Category(StrEnum):
    SELF = "self"
    CONTRIBUTION = "contribution"
    WORK = "work"
    REST = "rest"


class EnergyType(StrEnum):
    PHYSICAL = "physical"
    COGNITIVE = "cognitive"
    SOCIAL = "social"
    VALUES = "values"


class ActorType(StrEnum):
    """Who made a change: the owner on a screen, or a proposal the owner approved."""

    USER_UI = "user_ui"
    AI = "ai"


class MessageKind(StrEnum):
    DIALOGUE_USER = "dialogue_user"
    DIALOGUE_ASSISTANT = "dialogue_assistant"
    CUE = "cue"
    SUMMARY = "summary"
    UI_INPUT = "ui_input"
    DASHBOARD = "dashboard"
    EDITOR = "editor"
    APPROVAL = "approval"
    RECEIPT = "receipt"
    # Transient progress the sender deletes again, never part of the conversation.
    STATUS = "status"
    ERROR = "error"


class ChangeAction(StrEnum):
    """What one change does. Every mutation tool's `mode` is one of these, spelled the same.

    The tools declare their own subset, so a tool offers only the actions its entity has.
    """

    CREATE = "create"
    UPDATE = "update"
    MOVE = "move"
    COMPLETE = "complete"
    CANCEL = "cancel"
    REOPEN = "reopen"
    ARCHIVE = "archive"
    DELETE = "delete"
    LINK = "link"
    UNLINK = "unlink"
