import logging
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db.supabase import get_supabase

logger = logging.getLogger(__name__)
PrincipalType = Literal["admin", "organizer"]
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    email: str | None
    principal_type: PrincipalType
    username: str | None = None
    name: str | None = None


def _unauthenticated() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing access token")


def _authenticated_user(access_token: str) -> Any:
    try:
        response = get_supabase().auth.get_user(access_token)
    except Exception as exc:
        # Supabase Auth reports rejected/expired JWTs as 4xx Auth API errors. Network
        # and service failures remain 503 so callers can distinguish a temporary outage.
        error_status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if error_status in (400, 401, 403) or type(exc).__name__ in {
            "AuthInvalidJwtError",
            "AuthInvalidCredentialsError",
        }:
            raise _unauthenticated() from None
        logger.exception("Supabase Auth token verification failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from None

    user = getattr(response, "user", None)
    if user is None or not getattr(user, "id", None):
        raise _unauthenticated()
    return user


def _account_row(table: str, user_id: str, columns: str) -> dict[str, Any] | None:
    try:
        response = get_supabase().table(table).select(columns).eq("id", user_id).limit(1).execute()
        rows = response.data or []
    except Exception:
        logger.exception("Could not resolve application authorization profile")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authorization service unavailable",
        ) from None
    return rows[0] if rows else None


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    """Verify a Supabase access token and map its user to an active app principal."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise _unauthenticated()

    auth_user = _authenticated_user(credentials.credentials)
    user_id = str(auth_user.id)
    email = getattr(auth_user, "email", None)

    admin = _account_row("admin_users", user_id, "id,username,is_active")
    if admin is not None:
        if not admin.get("is_active", False):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")
        return CurrentUser(
            user_id=user_id,
            email=email,
            principal_type="admin",
            username=admin.get("username"),
        )

    organizer = _account_row("organizers", user_id, "id,username,name,is_active")
    if organizer is not None:
        if not organizer.get("is_active", False):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")
        return CurrentUser(
            user_id=user_id,
            email=email,
            principal_type="organizer",
            username=organizer.get("username"),
            name=organizer.get("name"),
        )

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No event access is assigned to this user")


def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if current_user.principal_type != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def require_organizer(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if current_user.principal_type != "organizer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organizer access required")
    return current_user
