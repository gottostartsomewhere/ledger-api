from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import transfer_amount_sum, transfer_total
from app.models.account import Account, AccountStatus
from app.models.transaction import (
    EntryType,
    LedgerEntry,
    Transfer,
    TransferKind,
    TransferStatus,
)
from app.models.user import User
from app.schemas.transaction import (
    DepositRequest,
    PaginatedHistory,
    TransactionHistoryItem,
    TransferRequest,
    WithdrawRequest,
)
from app.services.account import AccountService
from app.services.exceptions import (
    AccountClosed,
    AccountForbidden,
    AccountFrozen,
    AccountNotFound,
    InsufficientFunds,
    SameAccountTransfer,
)
from app.services.fx import FxService
from app.services.outbox import record_event


class LedgerService:
    """Owns all money movement.

    Invariants enforced here:
      1. For every Transfer, SUM(DEBIT.amount) == SUM(CREDIT.amount) **per
         currency**. Same-currency transfers use 2 legs; cross-currency
         transfers use 4 legs routed through the per-currency SYSTEM accounts.
      2. USER account balances never go negative (CHECK constraint at DB).
      3. FROZEN accounts cannot originate debits; CLOSED accounts cannot be
         touched on either side.
      4. All balance mutations are done inside SELECT ... FOR UPDATE locks
         in deterministic UUID order — no deadlocks, no lost updates.
      5. Balance updates + ledger rows + outbox event commit in one DB tx.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.accounts = AccountService(db)
        self.fx = FxService(db)

    async def _lock_account(self, account_id: UUID) -> Account:
        result = await self.db.execute(
            select(Account).where(Account.id == account_id).with_for_update()
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise AccountNotFound("account not found")
        return account

    async def _lock_accounts_ordered(self, account_ids: list[UUID]) -> dict[UUID, Account]:
        ordered = sorted(set(account_ids))
        result = await self.db.execute(
            select(Account)
            .where(Account.id.in_(ordered))
            .order_by(Account.id)
            .with_for_update()
        )
        rows = {a.id: a for a in result.scalars().all()}
        for aid in ordered:
            if aid not in rows:
                raise AccountNotFound(f"account {aid} not found")
        return rows

    @staticmethod
    def _assert_can_debit(account: Account) -> None:
        if account.status == AccountStatus.CLOSED:
            raise AccountClosed(f"account {account.id} is closed")
        if account.status == AccountStatus.FROZEN:
            raise AccountFrozen(f"account {account.id} is frozen")

    @staticmethod
    def _assert_can_credit(account: Account) -> None:
        if account.status == AccountStatus.CLOSED:
            raise AccountClosed(f"account {account.id} is closed")

    def _post_leg(
        self,
        transfer: Transfer,
        account: Account,
        entry_type: EntryType,
        amount: Decimal,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            transfer_id=transfer.id,
            account_id=account.id,
            entry_type=entry_type,
            amount=amount,
            currency=account.currency,
        )
        if entry_type == EntryType.DEBIT:
            account.balance = (account.balance or Decimal("0")) - amount
        else:
            account.balance = (account.balance or Decimal("0")) + amount
        self.db.add(entry)
        return entry

    @staticmethod
    def _assert_balanced_per_currency(entries: list[LedgerEntry]) -> None:
        totals: dict[str, Decimal] = {}
        for e in entries:
            sign = Decimal("1") if e.entry_type == EntryType.CREDIT else Decimal("-1")
            totals[e.currency] = totals.get(e.currency, Decimal("0")) + sign * e.amount
        for currency, net in totals.items():
            assert net == 0, f"ledger unbalanced in {currency}: {net}"

    async def _emit_completed(self, transfer: Transfer, entries: list[LedgerEntry]) -> None:
        transfer_total.labels(
            kind=transfer.kind.value, currency=transfer.currency
        ).inc()
        transfer_amount_sum.labels(
            kind=transfer.kind.value, currency=transfer.currency
        ).inc(float(transfer.amount))
        await record_event(
            self.db,
            event_type=f"transfer.{transfer.kind.value.lower()}.completed",
            aggregate_id=transfer.id,
            payload={
                "transfer_id": str(transfer.id),
                "kind": transfer.kind.value,
                "status": transfer.status.value,
                "amount": str(transfer.amount),
                "currency": transfer.currency,
                "initiator_user_id": (
                    str(transfer.initiator_user_id)
                    if transfer.initiator_user_id
                    else None
                ),
                "entries": [
                    {
                        "account_id": str(e.account_id),
                        "entry_type": e.entry_type.value,
                        "currency": e.currency,
                        "amount": str(e.amount),
                    }
                    for e in entries
                ],
            },
        )

    async def deposit(self, user: User, payload: DepositRequest) -> Transfer:
        account = await self._lock_account(payload.account_id)
        if account.user_id != user.id:
            raise AccountForbidden("account does not belong to the current user")
        self._assert_can_credit(account)

        system_account = await self.accounts.get_or_create_system_account(account.currency)
        system_account = await self._lock_account(system_account.id)

        transfer = Transfer(
            kind=TransferKind.DEPOSIT,
            initiator_user_id=user.id,
            amount=payload.amount,
            currency=account.currency,
            description=payload.description,
            status=TransferStatus.PENDING,
        )
        self.db.add(transfer)
        await self.db.flush()

        debit = self._post_leg(transfer, system_account, EntryType.DEBIT, payload.amount)
        credit = self._post_leg(transfer, account, EntryType.CREDIT, payload.amount)
        entries = [debit, credit]
        self._assert_balanced_per_currency(entries)
        transfer.status = TransferStatus.COMPLETED

        await self._emit_completed(transfer, entries)
        await self.db.flush()
        transfer.entries = entries  # type: ignore[attr-defined]
        return transfer

    async def withdraw(self, user: User, payload: WithdrawRequest) -> Transfer:
        account = await self._lock_account(payload.account_id)
        if account.user_id != user.id:
            raise AccountForbidden("account does not belong to the current user")
        self._assert_can_debit(account)
        if account.balance < payload.amount:
            raise InsufficientFunds("insufficient funds for withdrawal")

        system_account = await self.accounts.get_or_create_system_account(account.currency)
        system_account = await self._lock_account(system_account.id)

        transfer = Transfer(
            kind=TransferKind.WITHDRAWAL,
            initiator_user_id=user.id,
            amount=payload.amount,
            currency=account.currency,
            description=payload.description,
            status=TransferStatus.PENDING,
        )
        self.db.add(transfer)
        await self.db.flush()

        debit = self._post_leg(transfer, account, EntryType.DEBIT, payload.amount)
        credit = self._post_leg(transfer, system_account, EntryType.CREDIT, payload.amount)
        entries = [debit, credit]
        self._assert_balanced_per_currency(entries)
        transfer.status = TransferStatus.COMPLETED

        await self._emit_completed(transfer, entries)
        await self.db.flush()
        transfer.entries = entries  # type: ignore[attr-defined]
        return transfer

    async def transfer(self, user: User, payload: TransferRequest) -> Transfer:
        if payload.from_account_id == payload.to_account_id:
            raise SameAccountTransfer("from and to accounts must differ")

        locked = await self._lock_accounts_ordered(
            [payload.from_account_id, payload.to_account_id]
        )
        src = locked[payload.from_account_id]
        dst = locked[payload.to_account_id]

        if src.user_id != user.id:
            raise AccountForbidden("source account does not belong to the current user")
        self._assert_can_debit(src)
        self._assert_can_credit(dst)
        if src.balance < payload.amount:
            raise InsufficientFunds("insufficient funds for transfer")

        same_currency = src.currency == dst.currency
        dst_amount = payload.amount
        if not same_currency:
            dst_amount, _rate = await self.fx.convert(
                payload.amount, src.currency, dst.currency
            )

        transfer = Transfer(
            kind=TransferKind.TRANSFER,
            initiator_user_id=user.id,
            amount=payload.amount,
            currency=src.currency,
            description=payload.description,
            status=TransferStatus.PENDING,
        )
        self.db.add(transfer)
        await self.db.flush()

        entries: list[LedgerEntry] = []
        if same_currency:
            entries.append(self._post_leg(transfer, src, EntryType.DEBIT, payload.amount))
            entries.append(self._post_leg(transfer, dst, EntryType.CREDIT, payload.amount))
        else:
            sys_src = await self.accounts.get_or_create_system_account(src.currency)
            sys_dst = await self.accounts.get_or_create_system_account(dst.currency)
            sys_src = await self._lock_account(sys_src.id)
            sys_dst = await self._lock_account(sys_dst.id)
            # src-currency leg: sender pays system_src
            entries.append(self._post_leg(transfer, src, EntryType.DEBIT, payload.amount))
            entries.append(self._post_leg(transfer, sys_src, EntryType.CREDIT, payload.amount))
            # dst-currency leg: system_dst pays receiver
            entries.append(self._post_leg(transfer, sys_dst, EntryType.DEBIT, dst_amount))
            entries.append(self._post_leg(transfer, dst, EntryType.CREDIT, dst_amount))

        self._assert_balanced_per_currency(entries)
        transfer.status = TransferStatus.COMPLETED

        await self._emit_completed(transfer, entries)
        await self.db.flush()
        transfer.entries = entries  # type: ignore[attr-defined]
        return transfer

    async def reverse(self, transfer_id: UUID, reason: str) -> Transfer:
        """Admin-only compensating entry. Never edits history — writes a new
        transfer with the opposite legs. If the original debited A and credited
        B, the reversal debits B and credits A."""
        original = await self.db.get(Transfer, transfer_id)
        if original is None:
            raise AccountNotFound("transfer not found")

        original_entries = list(
            (
                await self.db.scalars(
                    select(LedgerEntry).where(LedgerEntry.transfer_id == transfer_id)
                )
            ).all()
        )

        account_ids = sorted({e.account_id for e in original_entries})
        locked = await self._lock_accounts_ordered(account_ids)

        reversal = Transfer(
            kind=original.kind,
            initiator_user_id=original.initiator_user_id,
            amount=original.amount,
            currency=original.currency,
            description=f"[reversal of {original.id}] {reason}",
            status=TransferStatus.PENDING,
        )
        self.db.add(reversal)
        await self.db.flush()

        new_entries: list[LedgerEntry] = []
        for e in original_entries:
            acct = locked[e.account_id]
            flipped = EntryType.CREDIT if e.entry_type == EntryType.DEBIT else EntryType.DEBIT
            new_entries.append(self._post_leg(reversal, acct, flipped, e.amount))

        self._assert_balanced_per_currency(new_entries)
        reversal.status = TransferStatus.COMPLETED

        await self._emit_completed(reversal, new_entries)
        await self.db.flush()
        reversal.entries = new_entries  # type: ignore[attr-defined]
        return reversal

    async def history(
        self, user: User, account_id: UUID, limit: int, offset: int
    ) -> PaginatedHistory:
        account = await self.accounts.get_for_user(user, account_id)

        total = await self.db.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.account_id == account.id
            )
        )

        stmt = (
            select(LedgerEntry, Transfer)
            .join(Transfer, Transfer.id == LedgerEntry.transfer_id)
            .where(LedgerEntry.account_id == account.id)
            .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)

        items: list[TransactionHistoryItem] = []
        for entry, transfer in result.all():
            items.append(
                TransactionHistoryItem(
                    entry_id=entry.id,
                    transfer_id=entry.transfer_id,
                    account_id=entry.account_id,
                    entry_type=entry.entry_type,
                    amount=entry.amount,
                    currency=entry.currency,
                    kind=transfer.kind,
                    description=transfer.description,
                    created_at=entry.created_at,
                )
            )

        return PaginatedHistory(
            items=items,
            limit=limit,
            offset=offset,
            total=int(total or 0),
        )

    async def load_transfer_with_entries(self, transfer_id: UUID) -> Transfer:
        transfer = await self.db.get(Transfer, transfer_id)
        if transfer is None:
            raise AccountNotFound("transfer not found")
        entries = await self.db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.transfer_id == transfer_id)
            .order_by(LedgerEntry.entry_type)
        )
        transfer.entries = list(entries.all())  # type: ignore[attr-defined]
        return transfer
