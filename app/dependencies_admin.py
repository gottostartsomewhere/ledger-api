"""Admin-only gate. Admin status is controlled via `ADMIN_EMAILS` (comma-
separated) in env — keeps the permissioning out of the database for the same
reason you keep your `root` password off the login page."""
from fastapi import Depends

from app.config import get_settings
from app.dependencies import get_current_user
from app.models.user import User
from app.services.exceptions import Forbidden


async def require_admin(user: User = Depends(get_current_user)) -> User:
    admins = get_settings().admin_emails_set()
    if user.email.lower() not in admins:
        raise Forbidden("admin privileges required")
    return user
