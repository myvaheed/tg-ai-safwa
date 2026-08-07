from __future__ import annotations

from enum import StrEnum


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
    USER_TEXT = "user_text"
    AI = "ai"
    SYSTEM = "system"


class DraftStatus(StrEnum):
    EDITING = "editing"
    READY = "ready"
    REVIEWED = "reviewed"
    COMMITTED = "committed"
    DISCARDED = "discarded"
    EXPIRED = "expired"


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
    DRAFT_REVIEW = "draft_review"
    APPROVAL = "approval"
    RECEIPT = "receipt"
    RETROSPECTIVE_PNG = "retrospective_png"
    ERROR = "error"


class UiIntentType(StrEnum):
    APPROVAL = "approval"
    CLARIFICATION = "clarification"
    DRAFT_REVIEW = "draft_review"
    FEEDBACK = "feedback"
    WARNING = "warning"
    REMINDER = "reminder"
    NAVIGATION = "navigation"
    RESULT = "result"
    ERROR = "error"


TERMINAL_STAGES = {CardStage.DONE, CardStage.CANCELLED}
LIVE_STAGE_PRECEDENCE = {
    CardStage.BACKLOG: 1,
    CardStage.SPRINT: 2,
    CardStage.TODAY: 3,
}
EFFORT_POINTS = {1, 2, 3, 5, 8, 13}
