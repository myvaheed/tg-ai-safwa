from __future__ import annotations

from collections import defaultdict
from io import BytesIO

import matplotlib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from .features.cards.model import CardStage
from .models import (
    Card,
    CardCategory,
    CardEnergyType,
    CardValue,
    Sprint,
    SprintCommitment,
    Value,
)


async def retrospective_data(session: AsyncSession, sprint_id: int) -> dict:
    sprint = await session.get(Sprint, sprint_id)
    if sprint is None:
        raise ValueError("Sprint does not exist")
    commitments = list(
        await session.scalars(
            select(SprintCommitment).where(SprintCommitment.sprint_id == sprint_id)
        )
    )
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"committed": 0, "completed": 0})
    energies: dict[str, dict[str, int]] = defaultdict(lambda: {"committed": 0, "completed": 0})
    for item in commitments:
        category_names = list(
            await session.scalars(
                select(CardCategory.category).where(CardCategory.card_id == item.card_id)
            )
        )
        energy_names = list(
            await session.scalars(
                select(CardEnergyType.energy_type).where(CardEnergyType.card_id == item.card_id)
            )
        )
        for name in category_names:
            categories[name]["committed"] += item.effort_snapshot
            if item.result == CardStage.DONE.value:
                categories[name]["completed"] += item.effort_snapshot
        for name in energy_names:
            energies[name]["committed"] += item.effort_snapshot
            if item.result == CardStage.DONE.value:
                energies[name]["completed"] += item.effort_snapshot
    totals = {
        "committed": sum(i.effort_snapshot for i in commitments if i.scope_kind == "initial"),
        "added": sum(i.effort_snapshot for i in commitments if i.scope_kind == "added"),
        "removed": sum(i.effort_snapshot for i in commitments if i.removed_at),
        "completed": sum(
            i.effort_snapshot for i in commitments if i.result == CardStage.DONE.value
        ),
        "cancelled": sum(
            i.effort_snapshot for i in commitments if i.result == CardStage.CANCELLED.value
        ),
    }
    card_ids = [item.card_id for item in commitments]
    cards = (
        list(await session.scalars(select(Card).where(Card.id.in_(card_ids)))) if card_ids else []
    )
    effort_by_card = {item.card_id: item.effort_snapshot for item in commitments}
    result_by_card = {item.card_id: item.result for item in commitments}
    hard_time = {
        "committed": sum(effort_by_card[card.id] for card in cards if card.hard_time),
        "completed": sum(
            effort_by_card[card.id]
            for card in cards
            if card.hard_time and result_by_card[card.id] == CardStage.DONE.value
        ),
    }
    had_blocked_work = any(card.blocked for card in cards)
    active_value_ids = set(await session.scalars(select(Value.id).where(Value.active.is_(True))))
    active_value_cards = (
        set(
            await session.scalars(
                select(CardValue.card_id).where(
                    CardValue.card_id.in_(card_ids),
                    CardValue.value_id.in_(active_value_ids),
                )
            )
        )
        if card_ids and active_value_ids
        else set()
    )
    active_value_completed = sum(
        effort_by_card[card.id]
        for card in cards
        if card.id in active_value_cards and result_by_card[card.id] == CardStage.DONE.value
    )
    return {
        "sprint": sprint,
        "totals": totals,
        "categories": dict(categories),
        "energies": dict(energies),
        "hard_time": hard_time,
        "had_blocked_work": had_blocked_work,
        "active_value_completed": active_value_completed,
    }


def render_retrospective_png(data: dict) -> bytes:
    totals = data["totals"]
    categories = data["categories"]
    energies = data["energies"]
    fig, axes = plt.subplots(1, 3, figsize=(14, 5), constrained_layout=True)
    labels = ["Initial", "Added", "Removed", "Done", "Cancelled"]
    values = [
        totals["committed"],
        totals["added"],
        totals["removed"],
        totals["completed"],
        totals["cancelled"],
    ]
    axes[0].bar(labels, values, color=["#4c78a8", "#72b7b2", "#f2cf5b", "#54a24b", "#e45756"])
    axes[0].set_title("Sprint effort")
    axes[0].tick_params(axis="x", rotation=35)

    def grouped(axis, groups: dict, title: str) -> None:  # type: ignore[no-untyped-def]
        names = list(groups) or ["No tags"]
        committed = [groups.get(name, {}).get("committed", 0) for name in names]
        completed = [groups.get(name, {}).get("completed", 0) for name in names]
        positions = list(range(len(names)))
        axis.bar([p - 0.2 for p in positions], committed, width=0.4, label="Committed")
        axis.bar([p + 0.2 for p in positions], completed, width=0.4, label="Completed")
        axis.set_xticks(positions, names, rotation=35, ha="right")
        axis.set_title(title)
        axis.legend()

    grouped(axes[1], categories, "Categories (overlapping)")
    grouped(axes[2], energies, "Energy types (overlapping)")
    output = BytesIO()
    fig.savefig(output, format="png", dpi=150)
    plt.close(fig)
    return output.getvalue()


def retrospective_recommendations(data: dict) -> list[str]:
    totals = data["totals"]
    committed = totals["committed"] + totals["added"]
    completion = totals["completed"] / committed if committed else 0
    recommendations: list[str] = []
    if completion < 0.6 and committed:
        recommendations.append(
            "Plan a smaller next Sprint and protect the Today queue from scope growth."
        )
    elif completion > 0.9 and committed:
        recommendations.append(
            "Your commitment was realistic; use this Sprint as a capacity reference."
        )
    if totals["added"] > totals["committed"] * 0.25:
        recommendations.append(
            "Scope changed substantially; reserve explicit capacity for incoming work."
        )
    if totals["cancelled"]:
        recommendations.append(
            "Review cancelled Actions for unclear intent, obstacles, or poor timing."
        )
    hard_time = data.get("hard_time", {})
    if hard_time.get("committed", 0) and hard_time.get("completed", 0) < hard_time["committed"]:
        recommendations.append("Some Hard Time effort slipped; protect those time windows first.")
    if data.get("had_blocked_work"):
        recommendations.append("Review blocked work before committing the next Sprint.")
    if data.get("active_value_completed", 0) == 0:
        recommendations.append(
            "No completed effort linked to active Values; check alignment in Planning."
        )
    return recommendations or [
        "Keep observing your energy and blocked work before changing capacity."
    ]
