"""Look up FX rates stored in the fx_rates table. Rates are direction-specific
(USD→EUR and EUR→USD are separate rows) so admins can encode any spread they
like without the code guessing reciprocals."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fx import FxRate
from app.services.exceptions import FxRateMissing


class FxService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_rate(self, from_currency: str, to_currency: str) -> Decimal:
        if from_currency == to_currency:
            return Decimal("1")
        rate = await self.db.scalar(
            select(FxRate.rate).where(
                FxRate.from_currency == from_currency.upper(),
                FxRate.to_currency == to_currency.upper(),
            )
        )
        if rate is None:
            raise FxRateMissing(
                f"no FX rate configured for {from_currency}->{to_currency}"
            )
        return rate

    async def upsert_rate(
        self, from_currency: str, to_currency: str, rate: Decimal
    ) -> FxRate:
        from_c = from_currency.upper()
        to_c = to_currency.upper()
        existing = await self.db.scalar(
            select(FxRate).where(
                FxRate.from_currency == from_c, FxRate.to_currency == to_c
            )
        )
        if existing is None:
            existing = FxRate(from_currency=from_c, to_currency=to_c, rate=rate)
            self.db.add(existing)
        else:
            existing.rate = rate
        await self.db.flush()
        return existing

    async def convert(
        self, amount: Decimal, from_currency: str, to_currency: str
    ) -> tuple[Decimal, Decimal]:
        """Returns (converted_amount, rate). Rounded to 4 decimals to match
        the ledger's scale."""
        rate = await self.get_rate(from_currency, to_currency)
        converted = (amount * rate).quantize(Decimal("0.0001"))
        return converted, rate
