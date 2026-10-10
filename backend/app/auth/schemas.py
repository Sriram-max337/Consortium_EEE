from typing import Literal

from pydantic import BaseModel, SecretStr


class DevAuthTokenRequest(BaseModel):
    email: str
    password: SecretStr


class DevAuthTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int | None = None


class AuthMeResponse(BaseModel):
    authenticated: bool
    user_id: str
    email: str | None = None
    principal_type: Literal["admin", "organizer"]
    username: str | None = None
    name: str | None = None
