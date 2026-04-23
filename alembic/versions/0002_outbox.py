"""outbox_events table

Revision ID: 0002_outbox
Revises: 0001_initial
Create Date: 2026-04-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_outbox"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    outbox_status = postgresql.ENUM(
        "PENDING", "SENT", "FAILED", name="outbox_status"
    )
    outbox_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING", "SENT", "FAILED", name="outbox_status", create_type=False
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_outbox_status_created", "outbox_events", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_status_created", table_name="outbox_events")
    op.drop_table("outbox_events")
    sa.Enum(name="outbox_status").drop(op.get_bind(), checkfirst=True)
