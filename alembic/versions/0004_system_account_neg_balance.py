"""allow SYSTEM accounts to go negative

USER accounts must still be balance >= 0 (enforced by CHECK). SYSTEM accounts
represent money that entered/exited the ledger from outside (deposits,
withdrawals). Their per-currency balance swinging negative is not a solvency
signal — the ledger is balanced because SUM(DEBIT) == SUM(CREDIT) per currency
inside every Transfer by construction.

Revision ID: 0004_system_account_neg_balance
Revises: 0003_fx_freeze_webhooks
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004_system_account_neg_balance"
down_revision: Union[str, None] = "0003_fx_freeze_webhooks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_accounts_balance_nonnegative", "accounts", type_="check"
    )
    op.create_check_constraint(
        "ck_accounts_balance_nonnegative",
        "accounts",
        "account_type = 'SYSTEM' OR balance >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_accounts_balance_nonnegative", "accounts", type_="check"
    )
    op.create_check_constraint(
        "ck_accounts_balance_nonnegative",
        "accounts",
        "balance >= 0",
    )
