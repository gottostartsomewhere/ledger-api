"""fx_rates, account status, webhook_endpoints

Revision ID: 0003_fx_freeze_webhooks
Revises: 0002_outbox
Create Date: 2026-04-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_fx_freeze_webhooks"
down_revision: Union[str, None] = "0002_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # account.status
    status_enum = postgresql.ENUM(
        "ACTIVE", "FROZEN", "CLOSED", name="account_status"
    )
    status_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "accounts",
        sa.Column(
            "status",
            postgresql.ENUM(
                "ACTIVE", "FROZEN", "CLOSED", name="account_status", create_type=False
            ),
            nullable=False,
            server_default="ACTIVE",
        ),
    )

    # fx_rates
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("from_currency", sa.String(3), nullable=False),
        sa.Column("to_currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("from_currency", "to_currency", name="uq_fx_pair"),
    )
    op.create_index("ix_fx_from", "fx_rates", ["from_currency"])
    op.create_index("ix_fx_to", "fx_rates", ["to_currency"])

    # webhook_endpoints
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("secret", sa.String(128), nullable=False),
        sa.Column("events", postgresql.ARRAY(sa.String(100)), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_webhooks_user", "webhook_endpoints", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_webhooks_user", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
    op.drop_index("ix_fx_to", table_name="fx_rates")
    op.drop_index("ix_fx_from", table_name="fx_rates")
    op.drop_table("fx_rates")
    op.drop_column("accounts", "status")
    sa.Enum(name="account_status").drop(op.get_bind(), checkfirst=True)
