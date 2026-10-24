"""How the configured model carries on after the owner turned its Reminder into a tracker.

Opt-in with ``--live-provider``, as `test_remind_phrases_live.py` is, and run from the folder
that holds `.env`. Every turn is the live model, reasoning off: the Advisor, the mutator, the
Reminder's setup session and the tracker session. The owner presses "📊 Also track" and then
Save, so the reminder call gets the result of a Check. What is compared is what the request
does next: it answers, and nothing makes the Reminder again.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from advisor_e2e_helpers import save

from llm_gateway import OpenAICompatibleProvider
from safwa.bootstrap.modules import PROPOSALS
from safwa.features.reminders.tracker import ALSO_TRACK
from safwa.qa import qa_provider_settings
from tg_agent_shell.ai.outcome import AIOutcomeKind

pytestmark = [pytest.mark.e2e, pytest.mark.live_provider]

REASONING_EFFORT = "none"
PHRASES = (
    "Напоминай мне каждый день в 8 утра выпить стакан воды",
    "Remind me every evening at 21:00 to read for 20 minutes",
    "Напоминай по понедельникам в 9 планировать неделю",
)


@pytest.fixture(scope="session")
def settings():
    # Read before the harness moves into the worktree, which holds no `.env`.
    return replace(qa_provider_settings().ai_config(), reasoning_effort=REASONING_EFFORT)


class Recorded:
    """The live model, with every request it answered and its calls."""

    def __init__(self, live: OpenAICompatibleProvider) -> None:
        self.live = live
        self.requests = []
        self.tool_calls = []

    async def complete(self, request):
        turn = await self.live.complete(replace(request, reasoning_effort=REASONING_EFFORT))
        self.requests.append(request)
        self.tool_calls.extend(turn.tool_calls)
        for call in turn.tool_calls:
            print("  call:", call.name, call.arguments_json[:200])
        return turn

    async def aclose(self) -> None:
        await self.live.aclose()


@pytest.mark.parametrize("phrase", PHRASES, ids=[f"phrase-{n}" for n in range(1, len(PHRASES) + 1)])
async def test_the_request_carries_on_after_also_track(settings, e2e_harness, phrase) -> None:
    """RM-TRACK-039 — tests/brd/reminders.feature"""
    provider = Recorded(OpenAICompatibleProvider(settings))
    try:
        advisor, _ = e2e_harness.advisor([], provider_factory=lambda _: provider)
        outcome = await advisor.handle(phrase)
        assert outcome.proposal_id is not None, f"{phrase!r} proposed nothing: {outcome.message}"
        proposal = advisor.reviews.proposal(outcome.proposal_id)
        offered = [switch.label for _place, switch in PROPOSALS.offered_switches(proposal.changes)]
        assert offered == [ALSO_TRACK], f"{phrase!r} proposed {[c.entity for c in proposal.changes]}"

        async with e2e_harness.sessions() as session:
            await advisor.switch_proposal(session, outcome.proposal_id, 0)
        check = advisor.reviews.proposal(outcome.proposal_id).changes[0]
        print(f"  tracker: {check.values['title']!r}, {check.values['schedule']!r}")
        asked = len(provider.tool_calls)
        resumed, _affected = await save(advisor, outcome.proposal_id)
    finally:
        await provider.aclose()

    # What the mutator read back for its reminder call, in the last request that holds it.
    results = next(
        (
            found
            for request in reversed(provider.requests)
            if (
                found := [
                    message["content"]
                    for message in request.messages
                    if message.get("role") == "tool" and message.get("name") == "reminder"
                ]
            )
        ),
        [],
    )
    later = [call.name for call in provider.tool_calls[asked:]]
    print(f"{phrase!r}: reminder result {results}, then {later}, said {resumed.message!r}")
    assert resumed.kind is AIOutcomeKind.ANSWER, f"{phrase!r} went on with {later}"
    assert "reminder" not in later, f"{phrase!r} made the Reminder again: {later}"
