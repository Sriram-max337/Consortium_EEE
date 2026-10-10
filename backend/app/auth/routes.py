from typing import Any

from fastapi import APIRouter, Depends

from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.schemas import AuthMeResponse

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/me", response_model=AuthMeResponse, response_model_exclude_none=True)
def auth_me(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "authenticated": True,
        "user_id": current_user.user_id,
        "email": current_user.email,
        "principal_type": current_user.principal_type,
        "username": current_user.username,
        "name": current_user.name,
    }
