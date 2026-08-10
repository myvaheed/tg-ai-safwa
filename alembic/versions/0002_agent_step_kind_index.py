"""Index agent_steps.kind so suspended approval batches are looked up, not scanned.

Revision ID: 0002

``ProposalService``/``AIAdvisor`` resolve a queued proposal by selecting the newest
``approval_batch`` steps.  Without this index that select degrades into a full scan of
every agent step ever recorded.

The matching ``Card.source_instance_id`` foreign key gained ``ondelete="SET NULL"`` in the
same change.  SQLite cannot alter a foreign key in place, so that part applies to databases
created from the current metadata; an existing database keeps the old constraint until it is
recreated from a backup.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_steps_kind ON agent_steps (kind)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_steps_kind")
