"""Fresh Safwa schema baseline.

Revision ID: 0001

This project currently starts from an empty database; compatibility upgrades
from pre-baseline development schemas are intentionally not retained.
"""

from alembic import op
from safwa.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
