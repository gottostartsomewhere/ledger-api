from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account, AccountType
from app.models.user import User
from app.schemas.account import AccountCreateRequest
from app.services.exceptions import AccountForbidden, AccountNotFound

SYSTEM_ACCOUNT_NAME = "__system_cash__"


class AccountService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, user: User, payload: AccountCreateRequest) -> Account:
        account = Account(
            user_id=user.id,
            account_type=AccountType.USER,
            currency=payload.currency.upper(),
            name=payload.name,
        )
        self.db.add(account)
        await self.db.commit()
        await self.db.refresh(account)
        return account

    async def list_for_user(self, user: User) -> list[Account]:
        result = await self.db.scalars(
            select(Account).where(Account.user_id == user.id).order_by(Account.created_at)
        )
        return list(result.all())

    async def get_for_user(self, user: User, account_id: UUID) -> Account:
        account = await self.db.get(Account, account_id)
        if account is None or account.account_type != AccountType.USER:
            raise AccountNotFound("account not found")
        if account.user_id != user.id:
            raise AccountForbidden("account does not belong to the current user")
        return account

    async def get_or_create_system_account(self, currency: str) -> Account:
        currency = currency.upper()
        account = await self.db.scalar(
            select(Account).where(
                Account.account_type == AccountType.SYSTEM,
                Account.currency == currency,
            )
        )
        if account is not None:
            return account

        account = Account(
            user_id=None,
            account_type=AccountType.SYSTEM,
            currency=currency,
            name=SYSTEM_ACCOUNT_NAME,
        )
        self.db.add(account)
        await self.db.flush()
        return account
