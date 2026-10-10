from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.exceptions import APIError

from app.admin.game_config_support import (
    active_ingredients,
    assert_ingredient_configuration_editable,
    database_error,
    ingredient_details_for_order,
    load_game_state,
    validate_ingredient_order,
    validate_readiness,
)
from app.admin.game_schemas import (
    GameConfigResponse,
    IngredientCreate,
    IngredientPatch,
    IngredientResponse,
    MasterOrderRequest,
)
from app.auth.dependencies import CurrentUser, require_admin
from app.db.supabase import get_supabase

router = APIRouter(tags=["admin game configuration"])
INGREDIENT_COLUMNS = "id,name,emoji,is_active,created_at,updated_at"


@router.get("/admin/ingredients", response_model=list[IngredientResponse])
def list_ingredients(
    include_inactive: bool = Query(default=False),
    admin: CurrentUser = Depends(require_admin),
) -> list[dict[str, Any]]:
    del admin
    query = get_supabase().table("ingredients").select(INGREDIENT_COLUMNS)
    if not include_inactive:
        query = query.eq("is_active", True)
    try:
        result = query.order("name").execute()
    except APIError as exc:
        raise database_error("list ingredients", exc) from None
    return result.data or []


@router.post("/admin/ingredients", status_code=status.HTTP_201_CREATED, response_model=IngredientResponse)
def create_ingredient(
    payload: IngredientCreate,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    client = get_supabase()
    assert_ingredient_configuration_editable(client)
    now = datetime.now(timezone.utc).isoformat()
    try:
        result = (
            client.table("ingredients")
            .insert(
                {
                    "name": payload.name,
                    "emoji": payload.emoji,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            .select(INGREDIENT_COLUMNS)
            .execute()
        )
    except APIError as exc:
        if exc.code == "23505":
            raise HTTPException(status_code=409, detail="Ingredient name already exists") from None
        raise database_error("create ingredient", exc) from None
    if not result.data:
        raise HTTPException(status_code=500, detail="Ingredient could not be created")
    return result.data[0]


@router.patch("/admin/ingredients/{ingredient_id}", response_model=IngredientResponse)
def update_ingredient(
    ingredient_id: UUID,
    payload: IngredientPatch,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    client = get_supabase()
    assert_ingredient_configuration_editable(client)
    try:
        current = (
            client.table("ingredients")
            .select(INGREDIENT_COLUMNS)
            .eq("id", str(ingredient_id))
            .limit(1)
            .execute()
            .data
            or []
        )
    except APIError as exc:
        raise database_error("read ingredient", exc) from None
    if not current:
        raise HTTPException(status_code=404, detail="Ingredient not found")

    changes = payload.model_dump(exclude_unset=True)
    if current[0].get("is_active") and changes.get("is_active") is False:
        _ensure_ingredient_unreferenced(client, str(ingredient_id))
    changes["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        result = (
            client.table("ingredients")
            .update(changes)
            .eq("id", str(ingredient_id))
            .select(INGREDIENT_COLUMNS)
            .execute()
        )
    except APIError as exc:
        if exc.code == "23505":
            raise HTTPException(status_code=409, detail="Ingredient name already exists") from None
        raise database_error("update ingredient", exc) from None
    if not result.data:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    return result.data[0]


def _ensure_ingredient_unreferenced(client: Any, ingredient_id: str) -> None:
    try:
        state_rows = client.table("game_state").select("master_order").eq("id", 1).limit(1).execute().data or []
    except APIError as exc:
        raise database_error("check ingredient references", exc) from None
    if state_rows and ingredient_id in [str(value) for value in (state_rows[0].get("master_order") or [])]:
        raise HTTPException(status_code=409, detail="Ingredient is used by the master order")


@router.delete("/admin/ingredients/{ingredient_id}", response_model=IngredientResponse)
def deactivate_ingredient(
    ingredient_id: UUID,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    client = get_supabase()
    assert_ingredient_configuration_editable(client)
    try:
        current = (
            client.table("ingredients")
            .select(INGREDIENT_COLUMNS)
            .eq("id", str(ingredient_id))
            .limit(1)
            .execute()
            .data
            or []
        )
    except APIError as exc:
        raise database_error("read ingredient", exc) from None
    if not current:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    if current[0].get("is_active"):
        _ensure_ingredient_unreferenced(client, str(ingredient_id))
    try:
        result = (
            client.table("ingredients")
            .update({"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", str(ingredient_id))
            .select(INGREDIENT_COLUMNS)
            .execute()
        )
    except APIError as exc:
        raise database_error("deactivate ingredient", exc) from None
    if not result.data:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    return result.data[0]


@router.get("/admin/game-config", response_model=GameConfigResponse)
def get_game_config(admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    del admin
    client = get_supabase()
    state = load_game_state(client)
    order = [str(value) for value in (state.get("master_order") or [])]
    return {
        "master_order": order,
        "master_order_ingredients": ingredient_details_for_order(client, order),
        "readiness": validate_readiness(client, state),
    }


@router.put("/admin/game-config/master-order", response_model=GameConfigResponse)
def put_master_order(
    payload: MasterOrderRequest,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    client = get_supabase()
    state = assert_ingredient_configuration_editable(client)
    ids = validate_ingredient_order(payload.ingredient_ids, active_ingredients(client))
    try:
        result = (
            client.table("game_state")
            .update({"master_order": ids, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", 1)
            .select("id")
            .execute()
        )
    except APIError as exc:
        raise database_error("save master order", exc) from None
    if not result.data:
        raise HTTPException(status_code=503, detail="Game configuration singleton is unavailable")
    state = {**state, "master_order": ids}
    return {
        "master_order": ids,
        "master_order_ingredients": ingredient_details_for_order(client, ids),
        "readiness": validate_readiness(client, state),
    }
