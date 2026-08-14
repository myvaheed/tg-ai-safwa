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
    """The two settable answers to a Check. Pending is derived from a null outcome."""

    PASSED = "passed"
    MISSED = "missed"


class ScheduleKind(StrEnum):
    """How a Reminder repeats. Derived from the resolved parameters, never model-supplied."""

    ONCE = "once"
    INTERVAL = "interval"
    DAILY = "daily"
    WEEKLY = "weekly"


class RelevanceVerdict(StrEnum):
    """The relevance check answers "does this Reminder still make sense?", nothing else.

    There is deliberately no "not now": that would be a firing gate, and firing gates are
    quiet windows.  Both verdicts escalate — `irrelevant` is a flag on the escalation, not
    a silent delete.
    """

    TRIGGER = "trigger"
    IRRELEVANT = "irrelevant"


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


CHECK_OUTCOME_LABELS = {
    "pending": "Pending",
    CheckOutcome.PASSED.value: "Passed",
    CheckOutcome.MISSED.value: "Missed",
}
# A Check is answered with the Card lifecycle verbs: complete is Passed, cancel is Missed.
CHECK_ANSWER_ACTIONS = {
    "complete": CheckOutcome.PASSED.value,
    "cancel": CheckOutcome.MISSED.value,
}

TERMINAL_STAGES = {CardStage.DONE, CardStage.CANCELLED}
LIVE_STAGE_PRECEDENCE = {
    CardStage.BACKLOG: 1,
    CardStage.SPRINT: 2,
    CardStage.TODAY: 3,
}
