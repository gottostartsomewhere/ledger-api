"""Refresh-token store backed by Redis.

The refresh JWT carries a `jti` claim. We store `refresh:<jti> -> user_id` in
Redis with the token's TTL. To refresh an access token the client presents the
refresh JWT; we look up its jti in Redis, issue a new access+refresh pair, and
DELETE the old jti — that rotation means a leaked refresh token is only good
until the legitimate user refreshes once."""
from __future__ import annotations

from redis.asyncio import Redis

KEY_PREFIX = "refresh"


def _key(jti: str) -> str:
    return f"{KEY_PREFIX}:{jti}"


async def store(redis: Redis, jti: str, user_id: str, ttl_seconds: int) -> None:
    await redis.set(_key(jti), user_id, ex=ttl_seconds)


async def lookup(redis: Redis, jti: str) -> str | None:
    value = await redis.get(_key(jti))
    return value if value is not None else None


async def revoke(redis: Redis, jti: str) -> None:
    await redis.delete(_key(jti))


async def revoke_all_for_user(redis: Redis, user_id: str) -> int:
    """Best-effort: scans the keyspace for refresh:* entries belonging to the
    user. O(n) over total refresh tokens; fine at our scale, swap for a user
    index if this ever hot-spots."""
    deleted = 0
    async for key in redis.scan_iter(match=f"{KEY_PREFIX}:*", count=500):
        value = await redis.get(key)
        if value == user_id:
            await redis.delete(key)
            deleted += 1
    return deleted
