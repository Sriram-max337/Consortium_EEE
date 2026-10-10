import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from postgrest.exceptions import APIError

from app.admin.game_config_support import (
    WITCH_COUNT,
    active_ingredients,
    assert_configuration_editable,
    database_error,
    ensure_witch_capacity,
    validate_ingredient_order,
)
from app.admin.game_schemas import (
    WitchCreate,
    WitchDeleteResponse,
    WitchPatch,
    WitchResponse,
)
from app.admin.team_codes import hash_credential
from app.auth.dependencies import CurrentUser, require_admin
from app.db.supabase import get_supabase

router = APIRouter(prefix="/admin/witches", tags=["admin witches"])
WITCH_COLUMNS = "id,witch_id,label,witch_type,display_order,is_locked,created_at,updated_at"
ORGANIZER_COLUMNS = "id,name,witch_id,is_active"


def _attach_organizer_assignments(client: Any, witches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not witches:
        return witches
    try:
        result = client.table("organizers").select(ORGANIZER_COLUMNS).execute()
    except APIError as exc:
        raise database_error("read Witch organizer assignments", exc) from None
    assignments: dict[str, list[dict[str, Any]]] = {}
    for organizer in result.data or []:
        assignments.setdefault(str(organizer["witch_id"]), []).append(
            {
                "id": organizer["id"],
                "name": organizer.get("name"),
                "is_active": organizer.get("is_active", False),
            }
        )
    return [
        {**witch, "organizer_assignments": assignments.get(str(witch["id"]), [])}
        for witch in witches
    ]


@router.get("", response_model=list[WitchResponse])
def list_witches(admin: CurrentUser = Depends(require_admin)) -> list[dict[str, Any]]:
    del admin
    client = get_supabase()
    try:
        result = client.table("witches").select(WITCH_COLUMNS).order("witch_id").execute()
    except APIError as exc:
        raise database_error("list Witches", exc) from None
    return _attach_organizer_assignments(client, result.data or [])


@router.get("/{witch_uuid}", response_model=WitchResponse)
def get_witch(witch_uuid: UUID, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    del admin
    client = get_supabase()
    try:
        rows = (
            client.table("witches")
            .select(WITCH_COLUMNS)
            .eq("id", str(witch_uuid))
            .limit(1)
            .execute()
            .data
            or []
        )
    except APIError as exc:
        raise database_error("read Witch", exc) from None
    if not rows:
        raise HTTPException(status_code=404, detail="Witch not found")
    return _attach_organizer_assignments(client, rows)[0]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WitchResponse)
def create_witch(payload: WitchCreate, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    del admin
    client = get_supabase()
    assert_configuration_editable(client)
    ensure_witch_capacity(client, payload.witch_type)
    display_order = validate_ingredient_order(payload.display_order, active_ingredients(client))
    now = datetime.now(timezone.utc).isoformat()
    values = {
        "witch_id": payload.witch_id,
        "label": payload.label,
        "witch_type": payload.witch_type,
        # `password_hash` is a legacy NOT NULL column. Witch records are not
        # authentication principals; all sign-in uses the assigned organizer's
        # Supabase Auth profile, so this random hash is never used for login.
        "password_hash": hash_credential(secrets.token_urlsafe(48)),
        "display_order": display_order,
        "is_locked": False,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = client.table("witches").insert(values).select(WITCH_COLUMNS).execute()
    except APIError as exc:
        if exc.code == "23505":
            raise HTTPException(status_code=409, detail="Witch ID already exists") from None
        raise database_error("create Witch", exc) from None
    if not result.data:
        raise HTTPException(status_code=500, detail="Witch could not be created")
    return _attach_organizer_assignments(client, result.data)[0]


@router.patch("/{witch_uuid}", response_model=WitchResponse)
def update_witch(
    witch_uuid: UUID,
    payload: WitchPatch,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    client = get_supabase()
    assert_configuration_editable(client)
    try:
        existing = (
            client.table("witches")
            .select(WITCH_COLUMNS)
            .eq("id", str(witch_uuid))
            .limit(1)
            .execute()
            .data
            or []
        )
    except APIError as exc:
        raise database_error("read Witch", exc) from None
    if not existing:
        raise HTTPException(status_code=404, detail="Witch not found")

    changes = payload.model_dump(exclude_unset=True)
    target_type = changes.get("witch_type", existing[0]["witch_type"])
    if target_type != existing[0]["witch_type"]:
        ensure_witch_capacity(client, target_type, exclude_id=str(witch_uuid))
    if "display_order" in changes and changes["display_order"] is not None:
        changes["display_order"] = validate_ingredient_order(changes["display_order"], active_ingredients(client))
    changes["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        result = (
            client.table("witches")
            .update(changes)
            .eq("id", str(witch_uuid))
            .select(WITCH_COLUMNS)
            .execute()
        )
    except APIError as exc:
        if exc.code == "23505":
            raise HTTPException(status_code=409, detail="Witch ID already exists") from None
        raise database_error("update Witch", exc) from None
    if not result.data:
        raise HTTPException(status_code=404, detail="Witch not found")
    return _attach_organizer_assignments(client, result.data)[0]


@router.delete("/{witch_uuid}", response_model=WitchDeleteResponse)
def delete_witch(
    witch_uuid: UUID,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    client = get_supabase()
    try:
        rows = (
            client.table("witches")
            .select("id,witch_id")
            .eq("id", str(witch_uuid))
            .limit(1)
            .execute()
            .data
            or []
        )
    except APIError as exc:
        raise database_error("read Witch", exc) from None
    if not rows:
        raise HTTPException(status_code=404, detail="Witch not found")

    witch = rows[0]
    try:
        if client.table("organizers").select("id").eq("witch_id", str(witch_uuid)).limit(1).execute().data:
            raise HTTPException(status_code=409, detail="Witch is assigned to an organizer and cannot be deleted")
        if client.table("attempts").select("id").eq("witch_id", str(witch_uuid)).limit(1).execute().data:
            raise HTTPException(status_code=409, detail="Witch has attempt history and cannot be deleted")
        if client.table("winners").select("position").eq("witch_id", str(witch_uuid)).limit(1).execute().data:
            raise HTTPException(status_code=409, detail="Witch has winner history and cannot be deleted")

        assert_configuration_editable(client)
        count_result = client.table("witches").select("id", count="exact").execute()
        if (count_result.count or 0) <= WITCH_COUNT:
            raise HTTPException(
                status_code=409,
                detail="The three-Witch roster is fixed. Deletion is allowed only to remove an unreferenced excess legacy record.",
            )

        deleted = (
            client.table("witches")
            .delete()
            .eq("id", str(witch_uuid))
            .select("id")
            .execute()
        )
    except HTTPException:
        raise
    except APIError as exc:
        if exc.code == "23503":
            raise HTTPException(
                status_code=409,
                detail="Witch is still referenced by an organizer or gameplay history and cannot be deleted",
            ) from None
        raise database_error("delete Witch", exc) from None
    if not deleted.data:
        raise HTTPException(status_code=404, detail="Witch not found")
    return {"id": str(witch_uuid), "witch_id": witch["witch_id"], "deleted": True}


def _set_witch_lock(witch_uuid: UUID, locked: bool) -> dict[str, Any]:
    client = get_supabase()
    assert_configuration_editable(client)
    try:
        result = (
            client.table("witches")
            .update({"is_locked": locked, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", str(witch_uuid))
            .select(WITCH_COLUMNS)
            .execute()
        )
    except APIError as exc:
        raise database_error("change Witch lock state", exc) from None
    if not result.data:
        raise HTTPException(status_code=404, detail="Witch not found")
    return result.data[0]


@router.post("/{witch_uuid}/lock", response_model=WitchResponse)
def lock_witch(witch_uuid: UUID, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    del admin
    return _set_witch_lock(witch_uuid, True)


@router.post("/{witch_uuid}/unlock", response_model=WitchResponse)
def unlock_witch(witch_uuid: UUID, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    del admin
    return _set_witch_lock(witch_uuid, False)
