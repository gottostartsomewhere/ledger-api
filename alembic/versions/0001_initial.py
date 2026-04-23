"""initial schema: users, accounts, transfers, ledger_entries, idempotency_keys

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    account_type = postgresql.ENUM("USER", "SYSTEM", name="account_type")
    account_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "account_type",
            postgresql.ENUM("USER", "SYSTEM", name="account_type", create_type=False),
            nullable=False,
            server_default="USER",
        ),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column(
            "balance",
            sa.Numeric(precision=20, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("balance >= 0", name="ck_accounts_balance_nonnegative"),
    )
    op.create_index("ix_accounts_user_id", "accounts", ["user_id"])

    transfer_kind = postgresql.ENUM(
        "DEPOSIT", "WITHDRAWAL", "TRANSFER", name="transfer_kind"
    )
    transfer_kind.create(op.get_bind(), checkfirst=True)

    transfer_status = postgresql.ENUM(
        "PENDING", "COMPLETED", "FAILED", name="transfer_status"
    )
    transfer_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "kind",
            postgresql.ENUM(
                "DEPOSIT",
                "WITHDRAWAL",
                "TRANSFER",
                name="transfer_kind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "COMPLETED",
                "FAILED",
                name="transfer_status",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "initiator_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_transfers_initiator_user_id", "transfers", ["initiator_user_id"])

    entry_type = postgresql.ENUM("DEBIT", "CREDIT", name="entry_type")
    entry_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transfer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transfers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "entry_type",
            postgresql.ENUM("DEBIT", "CREDIT", name="entry_type", create_type=False),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
    )
    op.create_index(
        "ix_ledger_account_created",
        "ledger_entries",
        ["account_id", "created_at"],
    )
    op.create_index("ix_ledger_transfer", "ledger_entries", ["transfer_id"])

    op.create_table(
        "idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer, nullable=False),
        sa.Column("response_body", postgresql.JSONB, nullable=False),
        sa.Column(
            "transfer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transfers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_index("ix_ledger_transfer", table_name="ledger_entries")
    op.drop_index("ix_ledger_account_created", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_transfers_initiator_user_id", table_name="transfers")
    op.drop_table("transfers")
    op.drop_index("ix_accounts_user_id", table_name="accounts")
    op.drop_table("accounts")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    sa.Enum(name="entry_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="transfer_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="transfer_kind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="account_type").drop(op.get_bind(), checkfirst=True)
