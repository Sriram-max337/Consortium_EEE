from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

class OrganizerIngredientOption(BaseModel):
    id: UUID
    name: str
    emoji: str | None = None


class IngredientOrderCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredient_ids: list[UUID] = Field(min_length=10, max_length=10)

    @field_validator("ingredient_ids")
    @classmethod
    def require_distinct_ingredients(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("ingredient_ids must be unique")
        return value


class IngredientOrderCheckResponse(BaseModel):
    result: Literal["CORRECT", "INCORRECT"]
