from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.redis import get_redis
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    new_jti,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.services import tokens as token_store
from app.services.exceptions import EmailAlreadyRegistered, InvalidCredentials


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def register(self, payload: RegisterRequest) -> User:
        existing = await self.db.scalar(
            select(User).where(User.email == payload.email.lower())
        )
        if existing is not None:
            raise EmailAlreadyRegistered("email is already registered")

        user = User(
            email=payload.email.lower(),
            password_hash=hash_password(payload.password),
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def _issue_tokens(self, user: User) -> TokenResponse:
        settings = get_settings()
        redis = get_redis()

        access = create_access_token(subject=str(user.id))
        jti = new_jti()
        refresh, ttl = create_refresh_token(subject=str(user.id), jti=jti)
        await token_store.store(redis, jti, str(user.id), ttl)

        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=settings.jwt_access_ttl_minutes * 60,
        )

    async def login(self, payload: LoginRequest) -> TokenResponse:
        user = await self.db.scalar(
            select(User).where(User.email == payload.email.lower())
        )
        if user is None or not verify_password(payload.password, user.password_hash):
            raise InvalidCredentials("email or password is incorrect")
        return await self._issue_tokens(user)

    async def refresh(self, refresh_token: str) -> TokenResponse:
        try:
            payload = decode_refresh_token(refresh_token)
        except ValueError:
            raise InvalidCredentials("invalid or expired refresh token")

        jti = payload.get("jti")
        sub = payload.get("sub")
        if not jti or not sub:
            raise InvalidCredentials("malformed refresh token")

        redis = get_redis()
        stored_user_id = await token_store.lookup(redis, jti)
        if stored_user_id is None or stored_user_id != sub:
            raise InvalidCredentials("refresh token has been revoked")

        # rotate: the old jti is unusable from this point on
        await token_store.revoke(redis, jti)

        user = await self.db.get(User, payload["sub"])
        if user is None:
            raise InvalidCredentials("user no longer exists")
        return await self._issue_tokens(user)

    async def logout(self, refresh_token: str) -> None:
        try:
            payload = decode_refresh_token(refresh_token)
        except ValueError:
            return
        jti = payload.get("jti")
        if jti:
            await token_store.revoke(get_redis(), jti)
