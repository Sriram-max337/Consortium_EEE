from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class TeamMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    roll_number: str | None = None


class TeamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: str = Field(min_length=1)
    team_name: str = Field(min_length=1)
    college_name: str | None = None
    department: str | None = None
    year: str | None = None
    email: str | None = None
    phone: str | None = None
    emergency_contact: str | None = None
    team_code: SecretStr
    members: list[TeamMemberInput] = Field(default_factory=list, max_length=3)

    @field_validator("team_id", "team_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("team_code")
    @classmethod
    def require_team_code(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("team_code must not be blank")
        return value


class TeamPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_name: str | None = Field(default=None, min_length=1)
    college_name: str | None = None
    department: str | None = None
    year: str | None = None
    email: str | None = None
    phone: str | None = None
    emergency_contact: str | None = None
    members: list[TeamMemberInput] | None = Field(default=None, max_length=3)

    @field_validator("team_name")
    @classmethod
    def strip_team_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("team_name must not be blank")
        return value

    @model_validator(mode="after")
    def validate_patch(self) -> "TeamPatch":
        if not self.model_fields_set:
            raise ValueError("provide at least one editable field")
        if "team_name" in self.model_fields_set and self.team_name is None:
            raise ValueError("team_name cannot be null")
        if "members" in self.model_fields_set and self.members is None:
            raise ValueError("members must be a list")
        return self


class TeamMemberSummary(BaseModel):
    member_number: int | None = None
    name: str | None = None
    roll_number: str | None = None
    created_at: datetime | None = None


class RegistrationSummary(BaseModel):
    submitted_at: datetime | None = None
    transaction_id: str | None = None
    payment_status: str | None = None
    payment_screenshot: str | None = None
    whatsapp_status: str | None = None
    consent: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TeamSummary(BaseModel):
    team_id: str
    team_name: str
    college_name: str | None = None
    department: str | None = None
    year: str | None = None
    email: str | None = None
    phone: str | None = None
    emergency_contact: str | None = None
    is_active: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    members: list[TeamMemberSummary] = Field(default_factory=list)


class TeamDetail(TeamSummary):
    registration: RegistrationSummary | None = None


class TeamListResponse(BaseModel):
    items: list[TeamSummary]
    total: int
    limit: int
    offset: int


class TeamDeleteResponse(BaseModel):
    team_id: str
    is_active: bool


class ResetTeamCodeResponse(BaseModel):
    team_id: str
    team_code: str
