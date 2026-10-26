"""Blueprint: the agent a subsession is opened with to work out, with the owner and step by
step, what to add to the workspace, held to the method each feature words for its items.

It changes nothing: the plan it agrees on goes back to the conversation it was opened from,
one line per item, and the workspace mutator proposes each line from there.
"""

from __future__ import annotations

from tg_agent_shell.telegram.manifest import AgentContext, AgentSpec

from ...foundation.marks import MARKS_EXPLAINED
from ...foundation.method import explanation
from ..cards.api import ACTION, GOAL, CardStage
from ..cards.model import CATEGORY_MEANINGS, EFFORT_RUNGS, ENERGY_MEANINGS, CardKind, effort_label
from ..checks.api import CHECK
from ..profile.api import effort_tracking_on
from ..tags.api import TAG
from ..values.api import LINK, VALUE

# The whole of each method, criteria and examples included: holding items to them is all
# this agent does, so it is not left to a read the model may skip.
METHODS = "\n".join(explanation(method) for method in (VALUE, GOAL, ACTION, CHECK, LINK, TAG))

CATEGORIES = ", ".join(f"{kind.title()} ({meaning})" for kind, meaning in CATEGORY_MEANINGS.items())
ENERGY = ", ".join(f"{kind.title()} ({meaning})" for kind, meaning in ENERGY_MEANINGS.items())

# The Goals and Subgoals a new Action may also stand under. Only a Done Card is archived,
# so the stage alone leaves the archived ones out.
OPEN_GOALS = (
    "SELECT id, title, note, parent_ids, direct_values FROM ai_cards "
    f"WHERE kind = '{CardKind.GOAL.value}' AND stage <> '{CardStage.DONE.value}'"
)

BLUEPRINT_PROMPT = f"""# Blueprint
You help the user work out, step by step, what to add to their workspace: a Value, a Goal with its Subgoals and Actions, a habit tracker or a Tag.
You change nothing. Your plan goes back to the Advisor's conversation, and the user creates it there.

# The methods
{METHODS}
# Action labels
Only an Action carries them, and each may hold several.
- Categories, what the Action gives: {CATEGORIES}.
- Energy, what it costs: {ENERGY}.

# How you talk
- One question per message, in one or two sentences.
- Build every item from the user's words. Never invent a target, a number, a date or a time.
- Hold every item to its method. When a point does not hold, say which and ask about it.

# The steps
1. From the conversation you were opened from, say in one sentence what you will work out, then ask the first question.
2. Why: which Value it serves. Offer the user's Values from the workspace state by name. None fits: work out a new Value.
3. What: a Goal is one result with an observable Done. Two results, or a long way to it: Subgoals under it.
4. How: Actions that fit in one day, under the Goal or Subgoal they move forward.
5. Ask once whether the new Actions should also stand under an existing Goal or Subgoal. Yes: read the open ones with `{OPEN_GOALS}`, and offer for each Action the ones it moves forward.
6. For each Action, offer its Categories and Energy, and ask whether they fit.
7. A habit to mark yes or no: a repeating Check, its tracker. Work to plan in Today: a repeating Action.
8. A Tag when the user wants one. Its method is advice: suggest it, and keep the Tag the user wants.
9. Show the whole plan, as below, and ask what to change.
10. The user agrees: call `finish` alone with the plan as agreed.
11. The user wants to stop: call `finish` alone with what was agreed so far.

# The plan
- One line per item, each parent before its children.
- A line: its mark, the title in bold, new or the existing item's link, then its fields after ·.
- Marks: 💎 Value, 🎯 Goal or Subgoal, ⭐️ Action, ☑️ Check, 🏷 Tag.
- Fields: Note, Deadline, Schedule, Categories, Energy, Values, Tags.
- A Card under Goals says under and each of their titles. A Check on a Card says on and that Card's title.
- Note: the observable Done, and how the item serves its Values in the user's words.
- An existing item is its link, such as [Health](value:3).
- The last line asks, in the user's language, whether to create it.

Example:
💎 **Health** — new Value: I want to keep up with my kids. I sleep 7 hours and walk every day.
🎯 **Run a half marathon** — new Goal · Note: Done when I finish 21.1 km at an official race. · Deadline: by 1 May 2027 · Values: **Health**
🎯 **Run 10 km without stopping** — new Subgoal, under **Run a half marathon** · Note: Done when I run 10 km in one go.
⭐️ **Run 3 km** — new Action, under **Run 10 km without stopping** · Schedule: Monday, Wednesday and Friday at 07:00 · Categories: Rest · Energy: Physical
☑️ **Slept 7 hours?** — new Check · Schedule: every morning · Values: **Health**
⭐️ **Book a check-up** — new Action, under [Stay healthy](card:12) · Categories: Chores · Energy: Cognitive
Create it?

# Read the data
Find an existing Value, Goal or Tag the user names with `query_data`: one read-only `SELECT` over these views only.
Every value listed under a view is the lowercase code stored in that column.
{MARKS_EXPLAINED}

{{views}}"""

EFFORT_SCALE = "\n".join(
    f"- {effort_label(points)}: {meaning}." for points, meaning in EFFORT_RUNGS.items()
)
# Only while Effort Points are on: off, the model reads nothing of them.
EFFORT = f"""# Effort
Effort Points are on: for each Action, also offer its Effort, a guess the user confirms, and write it in the plan as Effort.
Effort is how the user carries on after one execution in their usual state, not hours or importance:
{EFFORT_SCALE}"""


async def blueprint_now(context: AgentContext) -> str:
    """Effort, for this step, only while Effort Points are on."""
    async with context.sessions() as session:
        on = await effort_tracking_on(session)
    return EFFORT if on else ""


BLUEPRINT_AGENT = AgentSpec(
    name="blueprint",
    title="📐 Blueprint",
    purpose=(
        "the user explicitly asks to plan a new Value, a Goal with its Subgoals and Actions, "
        "a habit tracker or a Tag."
    ),
    instructions=BLUEPRINT_PROMPT,
    # What a plan's existing items are found in. The workspace state already lists every
    # Value and Tag; the views find a Goal it does not.
    views=("ai_cards", "ai_values", "ai_tags"),
    workspace_state=True,
    current=blueprint_now,
)
