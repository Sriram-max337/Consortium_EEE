import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from postgrest.exceptions import APIError

from app.auth.dependencies import CurrentUser, require_organizer
from app.db.supabase import get_supabase
from app.organizer.schemas import (
    IngredientOrderCheckRequest,
    IngredientOrderCheckResponse,
    OrganizerIngredientOption,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/organizer", tags=["organizer ingredient checker"])


@router.get("/ingredients", response_model=list[OrganizerIngredientOption])
def list_active_ingredients(
    current_user: CurrentUser = Depends(require_organizer),
) -> list[dict[str, Any]]:
    """Return only active ingredient choices; never return the answer order."""
    del current_user
    try:
        response = (
            get_supabase()
            .table("ingredients")
            .select("id,name,emoji")
            .eq("is_active", True)
            .order("name")
            .execute()
        )
    except APIError as exc:
        logger.error("Could not list organizer ingredient choices (database_code=%s)", exc.code or "unknown")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ingredient choices are unavailable",
        ) from None
    return response.data or []


@router.post("/check-order", response_model=IngredientOrderCheckResponse)
def check_ingredient_order(
    payload: IngredientOrderCheckRequest,
    current_user: CurrentUser = Depends(require_organizer),
) -> dict[str, str]:
    """Compare one complete order on the server without persisting a gameplay record."""
    del current_user
    submitted = [str(ingredient_id) for ingredient_id in payload.ingredient_ids]
    client = get_supabase()
    try:
        active_rows = (
            client.table("ingredients")
            .select("id")
            .eq("is_active", True)
            .execute()
            .data
            or []
        )
        game_rows = (
            client.table("game_state")
            .select("master_order")
            .eq("id", 1)
            .limit(1)
            .execute()
            .data
            or []
        )
    except APIError as exc:
        logger.error("Ingredient order check failed (database_code=%s)", exc.code or "unknown")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The ingredient checker is temporarily unavailable",
        ) from None

    active_ids = {str(row["id"]) for row in active_rows}
    if len(set(submitted)) != 10:
        # Also enforced by the request model; keep the invariant explicit at the boundary.
        raise HTTPException(status_code=422, detail="Submit each active ingredient exactly once")
    if len(active_ids) != 10:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The checker requires exactly 10 active ingredients. Ask an administrator to review configuration.",
        )
    if set(submitted) != active_ids:
        raise HTTPException(status_code=422, detail="The order must contain all 10 currently active ingredients exactly once")
    if not game_rows:
        raise HTTPException(status_code=503, detail="The master order is not configured")

    master_order = [str(value) for value in (game_rows[0].get("master_order") or [])]
    if len(master_order) != 10 or len(set(master_order)) != 10 or set(master_order) != active_ids:
        raise HTTPException(status_code=409, detail="The master order is incomplete. Ask an administrator to review configuration.")

    return {"result": "CORRECT" if submitted == master_order else "INCORRECT"}
