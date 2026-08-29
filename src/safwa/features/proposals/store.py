"""Every review this process still owes the owner an answer to.

The store is the whole lifetime of a proposal. Ids come from a counter that only goes up,
so a screen still naming a resolved review — a consumed button, a message in the chat —
can never reach a later one; a restart empties the store and startup deletes the buttons
that pointed into it.
"""

from __future__ import annotations

from itertools import count

from .model import ApprovalBatch, ChangeProposal, ProposalChange


class ProposalStore:
    """The open reviews and the suspended batches waiting on them."""

    def __init__(self) -> None:
        self._proposals: dict[int, ChangeProposal] = {}
        # Newest last: a batch is found by walking back from the one opened most recently.
        self._batches: list[ApprovalBatch] = []
        self._ids = count(1)

    def open_proposal(
        self, *, message: str, workspace_revision: int, changes: list[ProposalChange]
    ) -> ChangeProposal:
        proposal = ChangeProposal(
            id=next(self._ids),
            message=message,
            workspace_revision=workspace_revision,
            changes=changes,
        )
        self._proposals[proposal.id] = proposal
        return proposal

    def proposal(self, proposal_id: int) -> ChangeProposal | None:
        return self._proposals.get(proposal_id)

    def end_proposal(self, proposal_id: int) -> None:
        """End one review, whichever way it ended: saved, discarded, interrupted, refused."""
        self._proposals.pop(proposal_id, None)

    def open_batch(self, batch: ApprovalBatch) -> None:
        self._batches.append(batch)

    def batch_for_proposal(self, proposal_id: int) -> ApprovalBatch | None:
        """The suspended batch this screen belongs to, if it is still waiting on an answer."""
        for batch in reversed(self._batches):
            if batch.state.item_for(proposal_id) is not None:
                return batch
        return None

    def batch_for_run(self, run_id: int) -> ApprovalBatch | None:
        return next((batch for batch in self._batches if batch.run_id == run_id), None)

    def close_batch(self, batch: ApprovalBatch) -> None:
        if batch in self._batches:
            self._batches.remove(batch)

    @property
    def open_proposals(self) -> tuple[ChangeProposal, ...]:
        return tuple(self._proposals.values())

    @property
    def open_batches(self) -> tuple[ApprovalBatch, ...]:
        return tuple(self._batches)

    @property
    def busy(self) -> bool:
        """Whether anything is still on screen, so nothing unasked-for may be raised over it."""
        return bool(self._proposals or self._batches)
