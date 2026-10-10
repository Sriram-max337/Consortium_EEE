import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.auth.schemas import DevAuthTokenRequest, DevAuthTokenResponse
from app.db.supabase import create_supabase_auth_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dev/auth", tags=["development token helper"])


@router.post("/token", response_model=DevAuthTokenResponse)
def dev_token(payload: DevAuthTokenRequest) -> dict[str, Any]:
    """Sign in through Supabase Auth and return its access token for local API testing."""
    try:
        auth_response = create_supabase_auth_client().auth.sign_in_with_password(
            {
                "email": payload.email,
                "password": payload.password.get_secret_value(),
            }
        )
    except Exception as exc:
        error_status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if error_status in (400, 401, 403) or type(exc).__name__ == "AuthInvalidCredentialsError":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            ) from None

        # Log only the exception class; never include request data or the exception
        # text, which could contain sensitive sign-in details.
        logger.error("Development Supabase sign-in failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from None

    session = getattr(auth_response, "session", None)
    if session is None or not getattr(session, "access_token", None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    return {
        "access_token": session.access_token,
        "token_type": "bearer",
        "expires_in": session.expires_in,
    }
