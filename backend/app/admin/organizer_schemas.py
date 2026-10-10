from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class OrganizerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: SecretStr
    username: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    witch_id: UUID

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not value or "@" not in value:
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip()
        if not value or not all(char.isalnum() or char in "._-" for char in value):
            raise ValueError("Username may contain letters, numbers, dots, underscores, and hyphens")
        return value

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value

    @field_validator("password")
    @classmethod
    def require_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("Password must not be blank")
        return value


class OrganizerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=3, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None
    witch_id: UUID | None = None

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value or not all(char.isalnum() or char in "._-" for char in value):
            raise ValueError("Username may contain letters, numbers, dots, underscores, and hyphens")
        return value

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value

    @model_validator(mode="after")
    def validate_patch(self) -> "OrganizerPatch":
        if not self.model_fields_set:
            raise ValueError("Provide at least one editable field")
        for field in ("username", "name", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if "witch_id" in self.model_fields_set and self.witch_id is None:
            raise ValueError("witch_id cannot be null; every organizer profile must map to a Witch")
        return self


class OrganizerAssignmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    witch_id: UUID


class OrganizerPasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr | None = None

    @field_validator("password")
    @classmethod
    def nonblank_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            raise ValueError("Password must not be blank")
        return value


class OrganizerResponse(BaseModel):
    id: UUID
    username: str | None = None
    name: str | None = None
    email: str | None = None
    is_active: bool
    witch_id: UUID | None = None
    witch_label: str | None = None


class OrganizerPasswordResetResponse(BaseModel):
    organizer_id: UUID
    temporary_password: str | None = None
