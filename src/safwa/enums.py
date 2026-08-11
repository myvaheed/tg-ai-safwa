from __future__ import annotations

from enum import StrEnum


class AIProvider(StrEnum):
    LMSTUDIO = "lmstudio"
    OPENROUTER = "openrouter"


class WorkspaceMode(StrEnum):
    PLANNING = "planning"
    SPRINT = "sprint"


class CardKind(StrEnum):
    GOAL = "goal"
    IDEA = "idea"
    ACTION = "action"


class CardStage(StrEnum):
    BACKLOG = "backlog"
    SPRINT = "sprint"
    TODAY = "today"
    DONE = "done"
    CANCELLED = "cancelled"


class Priority(StrEnum):
    CRITICAL = "critical"
    MEDIUM = "medium"
    LOW = "low"


class CheckOutcome(StrEnum):
    """The three settable answers to a Check. Pending is derived from a null outcome.

    `passed` rather than `checked` because "checked" reads as *the Check was performed*
    rather than *the state held*.  There is deliberately no `skipped`: that would mean
    "not answered", which is exactly what Pending already means.
    """

    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


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
    USER_UI = "user_ui"
    AI = "ai"
    SYSTEM = "system"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"
    FAILED = "failed"


class MessageKind(StrEnum):
    SESSION_START = "session_start"
    SUBSESSION_RESULT = "subsession_result"
    DIALOGUE_USER = "dialogue_user"
    DIALOGUE_ASSISTANT = "dialogue_assistant"
    REMINDER = "reminder"
    SUMMARY = "summary"
    COMMAND = "command"
    UI_INPUT = "ui_input"
    DASHBOARD = "dashboard"
    CARD_EDITOR = "card_editor"
    APPROVAL = "approval"
    RECEIPT = "receipt"
    RETROSPECTIVE_PNG = "retrospective_png"
    ERROR = "error"


# Enum values are not labels: `failed` reads harshly on a shopping list, so the stored
# value and the shown word are allowed to differ.
CHECK_OUTCOME_LABELS = {
    "pending": "Pending",
    CheckOutcome.PASSED.value: "Passed",
    CheckOutcome.FAILED.value: "Missed",
    CheckOutcome.NOT_APPLICABLE.value: "Not applicable",
}

TERMINAL_STAGES = {CardStage.DONE, CardStage.CANCELLED}
LIVE_STAGE_PRECEDENCE = {
    CardStage.BACKLOG: 1,
    CardStage.SPRINT: 2,
    CardStage.TODAY: 3,
}
