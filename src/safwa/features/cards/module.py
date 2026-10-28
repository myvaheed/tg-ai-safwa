"""Cards: the Goals and Actions the owner keeps, each under the Goals it serves."""

from __future__ import annotations

from tg_agent_shell.foundation.screens import ScreenSpec
from tg_agent_shell.proposals.api import SimilarItems
from tg_agent_shell.telegram.contributions import ScreenCommand
from tg_agent_shell.telegram.manifest import FeatureModule, ProposalContribution

from ..profile.model import UserProfile
from . import agent, proposal, telegram, views
from .action_review import ACTION_REVIEW_HOOK as ACTION_REVIEW_HOOK
from .api import EFFORT_POINTS_ITEM, EFFORT_POINTS_PAGE
from .goal_review import GOAL_CLARIFICATION_HOOK as GOAL_CLARIFICATION_HOOK
from .hooks import BLOCKER_HOOK as BLOCKER_HOOK
from .hooks import CLOSED_PARENTS_HOOK as CLOSED_PARENTS_HOOK
from .hooks import EFFORT_TRACKING_REMINDER_HOOK as EFFORT_TRACKING_REMINDER_HOOK
from .hooks import EMPTY_PARENTS_HOOK as EMPTY_PARENTS_HOOK
from .hooks import ENERGY_BALANCE_HOOK as ENERGY_BALANCE_HOOK
from .hooks import OPEN_UNDER_CLOSED_HOOK as OPEN_UNDER_CLOSED_HOOK
from .hooks import ORPHANS_HOOK as ORPHANS_HOOK
from .hooks import OVERDUE_HOOK as OVERDUE_HOOK
from .hooks import PARENT_COMPLETION_HOOK as PARENT_COMPLETION_HOOK
from .hooks import REST_TODAY_HOOK as REST_TODAY_HOOK
from .hooks import SCHEDULE_PLAN_HOOK as SCHEDULE_PLAN_HOOK
from .hooks import TIME_TRACKING_REMINDER_HOOK as TIME_TRACKING_REMINDER_HOOK
from .hooks import TODAY_MORNINGS_HOOK as TODAY_MORNINGS_HOOK
from .hooks import TODAY_OVERLOAD_HOOK as TODAY_OVERLOAD_HOOK
from .hooks import TODAY_STALE_HOOK as TODAY_STALE_HOOK
from .model import Card
from .telegram import (
    CARD_CALLBACK_ACTIONS,
    CARD_TEXT_INPUTS,
    card_citation_label,
    command_board,
    render_card,
    start_manual_card_creation,
)
from .telegram.done_gate import CLOSING_TIME_INPUT
from .telegram.effort_page import effort_points_label, open_effort_points_page
from .telegram.thoughts import command_thoughts, on_thought_list, process_thought, thought_actions

MODULE = FeatureModule(
    name="cards",
    proposals=(
        ProposalContribution(
            handler=proposal.CardProposalHandler(),
            tool=agent.ACTION_TOOL,
            autoapprovals=agent.CARD_AUTOAPPROVALS,
            presenter=telegram.CardProposalPresenter(),
            similar=SimilarItems(field="title", open_items=proposal.open_cards),
            editable=telegram.card_review_fields,
            actions=thought_actions,
        ),
    ),
    # A Goal and a Subgoal are Cards too: the same handler prepares and writes them.
    mutation_tools=(agent.GOAL_TOOL, agent.THOUGHT_TOOL),
    views=views.VIEWS,
    screens=(
        ScreenSpec(
            item_type="card",
            model=Card,
            open=render_card,
            label=card_citation_label,
            view="card_view",
        ),
        ScreenSpec(
            item_type=EFFORT_POINTS_ITEM,
            model=UserProfile,
            open=open_effort_points_page,
            label=effort_points_label,
            view=EFFORT_POINTS_PAGE,
        ),
    ),
    commands=(
        # Every stage side by side, the Backlog one tap further; Today is on Home itself.
        ScreenCommand(handler=command_board, nav="board", title="🗂 Dashboard"),
        # Add is a menu button and nothing else: a Card is created on a screen.
        ScreenCommand(
            handler=start_manual_card_creation, nav="add", title="➕ Add"
        ),
        ScreenCommand(handler=command_thoughts, nav="thoughts", title="💭 Thoughts"),
    ),
    callback_actions={
        **CARD_CALLBACK_ACTIONS,
        "card_thought_process": process_thought,
        "thought_list": on_thought_list,
    },
    text_inputs=(*CARD_TEXT_INPUTS, CLOSING_TIME_INPUT),
)
