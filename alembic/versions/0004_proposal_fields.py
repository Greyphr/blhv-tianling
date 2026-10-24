"""add proposal fields

Revision ID: 0004_proposal_fields
Revises: 0003_member_penalties
Create Date: 2026-10-23 02:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_proposal_fields"
down_revision = "0003_member_penalties"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("queue_entries", sa.Column("proposal_id", sa.String(length=64), nullable=True))
    op.add_column("queue_entries", sa.Column("either_group", sa.String(length=64), nullable=True))
    op.add_column("queue_entries", sa.Column("sibling_entry_ids", sa.JSON(), nullable=True))
    op.create_index(op.f("ix_queue_entries_proposal_id"), "queue_entries", ["proposal_id"], unique=False)
    op.create_index(op.f("ix_queue_entries_either_group"), "queue_entries", ["either_group"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_queue_entries_either_group"), table_name="queue_entries")
    op.drop_index(op.f("ix_queue_entries_proposal_id"), table_name="queue_entries")
    op.drop_column("queue_entries", "sibling_entry_ids")
    op.drop_column("queue_entries", "either_group")
    op.drop_column("queue_entries", "proposal_id")
