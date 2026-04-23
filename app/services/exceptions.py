class LedgerError(Exception):
    """Base class for domain-level ledger errors."""

    status_code: int = 400
    code: str = "ledger_error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.code)
        self.detail = detail


class EmailAlreadyRegistered(LedgerError):
    status_code = 409
    code = "email_already_registered"


class InvalidCredentials(LedgerError):
    status_code = 401
    code = "invalid_credentials"


class AccountNotFound(LedgerError):
    status_code = 404
    code = "account_not_found"


class AccountForbidden(LedgerError):
    status_code = 403
    code = "account_forbidden"


class InsufficientFunds(LedgerError):
    status_code = 422
    code = "insufficient_funds"


class CurrencyMismatch(LedgerError):
    status_code = 422
    code = "currency_mismatch"


class SameAccountTransfer(LedgerError):
    status_code = 422
    code = "same_account_transfer"


class IdempotencyConflict(LedgerError):
    status_code = 409
    code = "idempotency_key_conflict"


class AccountFrozen(LedgerError):
    status_code = 423
    code = "account_frozen"


class AccountClosed(LedgerError):
    status_code = 423
    code = "account_closed"


class FxRateMissing(LedgerError):
    status_code = 422
    code = "fx_rate_missing"


class Forbidden(LedgerError):
    status_code = 403
    code = "forbidden"
