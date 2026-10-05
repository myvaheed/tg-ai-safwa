"""Domain fixtures with the Scheduler's terminal result supplied explicitly."""

from __future__ import annotations

from types import SimpleNamespace

from safwa.features.cards.use_cases import create_card as domain_card
from safwa.features.checks.use_cases import create_check as domain_check
from safwa.features.reminders.api import parse_phrase, schedule_payload
from safwa.features.schedules.api import workspace_zone
from tg_agent_shell.foundation.clock import utcnow


async def rule_for(session, text):
    """The rule the Scheduler reads a hand-written clock phrase as."""
    if not text:
        return None
    if text == "after completion":
        return {"kind": "after_completion"}
    return {
        "kind": "fixed",
        "timing": schedule_payload(
            parse_phrase(text, now=utcnow(), tz=await workspace_zone(session))
        ),
    }


async def create_card(session, **fields):
    fields.setdefault("schedule_rule", await rule_for(session, fields.get("schedule")))
    return await domain_card(session, **fields)


async def create_check(session, **fields):
    fields.setdefault("schedule_rule", await rule_for(session, fields.get("schedule")))
    return await domain_check(session, **fields)


class ScriptedCompiler:
    """The Scheduler's answer for each text; any other text gets a question."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def compile(self, text, submitted_at, tz, target):
        self.calls.append((text, target))
        return self.answers.get(text, (None, f"When does {text} happen?"))


def with_compiler(services, answers):
    """Telegram services whose editors compile a typed Schedule with these answers."""
    compiler = ScriptedCompiler(answers)
    services.features = SimpleNamespace(schedule_compiler=compiler)
    return compiler
