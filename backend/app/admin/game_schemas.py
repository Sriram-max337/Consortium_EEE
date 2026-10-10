from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class IngredientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    emoji: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class IngredientPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    emoji: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @model_validator(mode="after")
    def validate_patch(self) -> "IngredientPatch":
        if not self.model_fields_set:
            raise ValueError("provide at least one editable field")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if "is_active" in self.model_fields_set and self.is_active is None:
            raise ValueError("is_active cannot be null")
        return self


class IngredientResponse(BaseModel):
    id: UUID
    name: str
    emoji: str | None = None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MasterOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredient_ids: list[UUID]


class MasterOrderIngredient(BaseModel):
    id: UUID
    name: str | None = None
    emoji: str | None = None
    is_active: bool | None = None


class ConfigurationReadiness(BaseModel):
    is_ready: bool
    issues: list[str]
    active_ingredient_count: int
    master_order_valid: bool


class GameConfigResponse(BaseModel):
    master_order: list[UUID]
    master_order_ingredients: list[MasterOrderIngredient]
    readiness: ConfigurationReadiness


class WitchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    witch_id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    witch_type: Literal["real", "fake"]
    display_order: list[UUID]

    @field_validator("witch_id")
    @classmethod
    def nonblank_witch_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("witch_id must not be blank")
        return value

    @field_validator("label")
    @classmethod
    def nonblank_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be blank")
        return value

class WitchPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    witch_id: str | None = Field(default=None, min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=200)
    witch_type: Literal["real", "fake"] | None = None
    display_order: list[UUID] | None = None
    is_locked: bool | None = None

    @field_validator("witch_id")
    @classmethod
    def nonblank_witch_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("witch_id must not be blank")
        return value

    @model_validator(mode="after")
    def validate_patch(self) -> "WitchPatch":
        if not self.model_fields_set:
            raise ValueError("provide at least one editable field")
        if self.witch_id is None and "witch_id" in self.model_fields_set:
            raise ValueError("witch_id cannot be null")
        if self.witch_type is None and "witch_type" in self.model_fields_set:
            raise ValueError("witch_type cannot be null")
        if self.is_locked is None and "is_locked" in self.model_fields_set:
            raise ValueError("is_locked cannot be null")
        return self


class WitchOrganizerAssignment(BaseModel):
    id: UUID
    name: str | None = None
    is_active: bool


class WitchResponse(BaseModel):
    id: UUID
    witch_id: str
    label: str | None = None
    witch_type: Literal["real", "fake"]
    display_order: list[UUID] | None = None
    is_locked: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    organizer_assignments: list[WitchOrganizerAssignment] = Field(default_factory=list)


class WitchDeleteResponse(BaseModel):
    id: UUID
    witch_id: str
    deleted: bool
