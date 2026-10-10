import logging
import secrets
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from postgrest.exceptions import APIError

from app.admin.organizer_schemas import (
    OrganizerAssignmentUpdate,
    OrganizerCreate,
    OrganizerPasswordResetRequest,
    OrganizerPasswordResetResponse,
    OrganizerPatch,
    OrganizerResponse,
)
from app.auth.dependencies import CurrentUser, require_admin
from app.config import get_settings
from app.db.supabase import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/organizers", tags=["admin organizers"])
PROFILE_COLUMNS = "id,username,name,witch_id,is_active"
MAX_ACTIVE_WITCH_ORGANIZERS = 3


def _auth_user(response: Any) -> Any:
    return getattr(response, "user", response)


def _safe_supabase_project_ref() -> str:
    hostname = urlsplit(get_settings().supabase_url).hostname or ""
    if hostname.endswith(".supabase.co"):
        return hostname.removesuffix(".supabase.co")
    return "custom-or-unset"


def _is_username_conflict(exc: APIError) -> bool:
    description = f"{getattr(exc, 'message', '')} {getattr(exc, 'details', '')} {getattr(exc, 'hint', '')}".lower()
    return "username" in description or "organizers_username_case_insensitive_key" in description


def _profile_schema_error(exc: APIError, operation: str) -> HTTPException:
    if exc.code == "42703":
        return HTTPException(
            status_code=503,
            detail="Organizer management schema is not installed. Apply backend/sql/admin_organizer_management.sql first.",
        )
    logger.error("Organizer profile %s failed (code=%s)", operation, exc.code)
    return HTTPException(status_code=503, detail="Organizer profile is unavailable")


def _auth_failure(exc: Exception, operation: str) -> HTTPException:
    code = str(getattr(exc, "code", "") or "").lower()
    message = str(getattr(exc, "message", "") or exc).lower()
    error_status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if code in {"email_exists", "user_already_exists"} or any(
        phrase in message for phrase in ("already registered", "already exists", "email exists")
    ):
        return HTTPException(status_code=409, detail="An account with this email already exists")
    if error_status == 404:
        return HTTPException(status_code=404, detail="Organizer Auth account not found")
    logger.error("Organizer Auth operation %s failed (%s)", operation, type(exc).__name__)
    return HTTPException(status_code=503, detail="Organizer authentication service is unavailable")


def _read_profile(client: Any, organizer_id: UUID | str) -> dict[str, Any]:
    try:
        rows = (
            client.table("organizers")
            .select(PROFILE_COLUMNS)
            .eq("id", str(organizer_id))
            .limit(1)
            .execute()
            .data
            or []
        )
    except APIError as exc:
        raise _profile_schema_error(exc, "lookup") from None
    if not rows:
        raise HTTPException(status_code=404, detail="Organizer not found")
    return rows[0]


def _get_auth_user(client: Any, organizer_id: str) -> Any:
    try:
        return _auth_user(client.auth.admin.get_user_by_id(organizer_id))
    except Exception as exc:
        raise _auth_failure(exc, "read user") from None


def _ensure_auth_email_available(client: Any, email: str) -> None:
    """Reject retries for an Auth account that may have survived an earlier failure."""
    page = 1
    page_size = 100
    try:
        while True:
            users = client.auth.admin.list_users(page=page, per_page=page_size)
            if any(str(getattr(user, "email", "") or "").strip().casefold() == email.casefold() for user in users):
                raise HTTPException(
                    status_code=409,
                    detail="An Auth account with this email already exists. Check for an existing organizer profile before retrying.",
                )
            if len(users) < page_size:
                return
            page += 1
    except HTTPException:
        raise
    except Exception as exc:
        raise _auth_failure(exc, "check existing user") from None


def _cleanup_failed_organizer_creation(client: Any, organizer_id: str) -> bool:
    cleanup_failed = False
    try:
        client.table("organizers").delete().eq("id", organizer_id).execute()
    except Exception:
        cleanup_failed = True
        logger.error("Organizer profile cleanup failed after creation error")
    try:
        client.auth.admin.delete_user(organizer_id)
    except Exception:
        cleanup_failed = True
        logger.error("Auth cleanup failed after organizer profile creation error")
    return not cleanup_failed


def _ensure_username_available(client: Any, username: str, exclude_id: str | None = None) -> None:
    normalized = username.casefold()
    try:
        admin_rows = client.table("admin_users").select("id,username").execute().data or []
        organizer_rows = client.table("organizers").select("id,username").execute().data or []
    except APIError as exc:
        if exc.code == "42703":
            raise _profile_schema_error(exc, "username lookup") from None
        logger.error("Username lookup failed (code=%s)", exc.code)
        raise HTTPException(status_code=503, detail="Username availability could not be checked") from None
    for row in [*admin_rows, *organizer_rows]:
        if str(row.get("id")) != exclude_id and str(row.get("username") or "").strip().casefold() == normalized:
            raise HTTPException(status_code=409, detail="Username is already in use")


def _witch_label_map(client: Any) -> dict[str, str | None]:
    try:
        rows = client.table("witches").select("id,label").execute().data or []
    except APIError as exc:
        logger.error("Witch lookup for organizer management failed (code=%s)", exc.code)
        raise HTTPException(status_code=503, detail="Witch assignments are unavailable") from None
    return {str(row["id"]): row.get("label") for row in rows}


def _ensure_witch_available(client: Any, witch_id: UUID | str, organizer_id: str | None = None) -> None:
    witch_key = str(witch_id)
    try:
        rows = (
            client.table("witches")
            .select("id")
            .eq("id", witch_key)
            .limit(1)
            .execute()
            .data
            or []
        )
    except APIError as exc:
        logger.error("Witch assignment validation failed (code=%s)", exc.code)
        raise HTTPException(status_code=503, detail="Witch assignment could not be validated") from None
    if not rows:
        raise HTTPException(status_code=404, detail="Witch not found")
    try:
        query = client.table("organizers").select("id").eq("witch_id", witch_key)
        if organizer_id:
            query = query.neq("id", organizer_id)
        assigned = query.limit(1).execute().data or []
    except APIError as exc:
        logger.error("Witch assignment conflict check failed (code=%s)", exc.code)
        raise HTTPException(status_code=503, detail="Witch assignment could not be checked") from None
    if assigned:
        raise HTTPException(status_code=409, detail="This Witch is already assigned to another organizer")


def _ensure_active_organizer_capacity(client: Any, organizer_id: str | None = None) -> None:
    try:
        query = client.table("organizers").select("id").eq("is_active", True)
        if organizer_id:
            query = query.neq("id", organizer_id)
        active_profiles = query.execute().data or []
    except APIError as exc:
        logger.error("Active organizer capacity check failed (code=%s)", exc.code)
        raise HTTPException(status_code=503, detail="Organizer capacity could not be checked") from None
    if len(active_profiles) >= MAX_ACTIVE_WITCH_ORGANIZERS:
        raise HTTPException(status_code=409, detail="The Witch Hunt has exactly three active organizer profiles")


def _ensure_organizer_profile_capacity(client: Any) -> None:
    try:
        profiles = client.table("organizers").select("id").execute().data or []
    except APIError as exc:
        logger.error("Organizer profile capacity check failed (code=%s)", exc.code)
        raise HTTPException(status_code=503, detail="Organizer profile capacity could not be checked") from None
    if len(profiles) >= MAX_ACTIVE_WITCH_ORGANIZERS:
        raise HTTPException(status_code=409, detail="The Witch Hunt has exactly three organizer profiles")


def _response(client: Any, profile: dict[str, Any], auth_user: Any, labels: dict[str, str | None] | None = None) -> dict[str, Any]:
    witch_id = str(profile["witch_id"]) if profile.get("witch_id") is not None else None
    label_map = labels if labels is not None else _witch_label_map(client)
    return {
        "id": str(profile["id"]),
        "username": profile.get("username"),
        "name": profile.get("name"),
        "email": getattr(auth_user, "email", None),
        "is_active": bool(profile.get("is_active", False)),
        "witch_id": witch_id,
        "witch_label": label_map.get(witch_id) if witch_id else None,
    }


@router.get("", response_model=list[OrganizerResponse])
def list_organizers(admin: CurrentUser = Depends(require_admin)) -> list[dict[str, Any]]:
    del admin
    client = get_supabase()
    try:
        profiles = client.table("organizers").select(PROFILE_COLUMNS).execute().data or []
    except APIError as exc:
        raise _profile_schema_error(exc, "list") from None
    labels = _witch_label_map(client)
    result = []
    for profile in profiles:
        auth_user = _get_auth_user(client, str(profile["id"]))
        result.append(_response(client, profile, auth_user, labels))
    return sorted(result, key=lambda row: ((row["username"] or "").casefold(), row["id"]))


@router.get("/{organizer_uuid}", response_model=OrganizerResponse)
def get_organizer(organizer_uuid: UUID, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    del admin
    client = get_supabase()
    profile = _read_profile(client, organizer_uuid)
    auth_user = _get_auth_user(client, str(organizer_uuid))
    return _response(client, profile, auth_user)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=OrganizerResponse)
def create_organizer(payload: OrganizerCreate, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    del admin
    request_fields = sorted(payload.model_fields_set)
    username_present = payload.username is not None
    username_nonblank = bool(payload.username and payload.username.strip())
    name_present = payload.name is not None
    name_nonblank = bool(payload.name and payload.name.strip())
    logger.info(
        "Organizer creation request build_id=%s supabase_project_ref=%s fields=%s username_present=%s username_nonblank=%s name_present=%s name_nonblank=%s witch_id_present=%s",
        get_settings().app_build_id,
        _safe_supabase_project_ref(),
        request_fields,
        username_present,
        username_nonblank,
        name_present,
        name_nonblank,
        payload.witch_id is not None,
    )
    if not username_nonblank or not name_nonblank:
        raise HTTPException(status_code=422, detail="Username and name are required and cannot be blank")
    if payload.witch_id is None:
        raise HTTPException(status_code=422, detail="Assign this organizer to one of the three Witch identities")

    client = get_supabase()
    _ensure_organizer_profile_capacity(client)
    _ensure_active_organizer_capacity(client)
    _ensure_username_available(client, payload.username)
    _ensure_auth_email_available(client, payload.email)
    _ensure_witch_available(client, payload.witch_id)
    labels = _witch_label_map(client)
    try:
        auth_user = _auth_user(
            client.auth.admin.create_user(
                {
                    "email": payload.email,
                    "password": payload.password.get_secret_value(),
                    "email_confirm": True,
                }
            )
        )
    except Exception as exc:
        raise _auth_failure(exc, "create user") from None
    organizer_id = str(auth_user.id)
    logger.info(
        "Organizer Auth create succeeded build_id=%s supabase_project_ref=%s auth_user_id_present=%s",
        get_settings().app_build_id,
        _safe_supabase_project_ref(),
        bool(organizer_id),
    )
    profile_values = {
        "id": organizer_id,
        "username": payload.username,
        "name": payload.name,
        "witch_id": str(payload.witch_id),
        "is_active": True,
    }
    logger.info(
        "Organizer profile insert build_id=%s supabase_project_ref=%s columns=%s username_nonblank=%s name_nonblank=%s",
        get_settings().app_build_id,
        _safe_supabase_project_ref(),
        sorted(profile_values),
        bool(profile_values["username"].strip()),
        bool(profile_values["name"].strip()),
    )
    try:
        created = client.table("organizers").insert(profile_values).select(PROFILE_COLUMNS).execute().data or []
        if not created:
            raise HTTPException(status_code=500, detail="Organizer profile was not returned after creation")
    except HTTPException:
        if not _cleanup_failed_organizer_creation(client, organizer_id):
            raise HTTPException(
                status_code=500,
                detail="Organizer profile creation failed and cleanup needs administrator attention",
            ) from None
        raise
    except APIError as exc:
        if not _cleanup_failed_organizer_creation(client, organizer_id):
            raise HTTPException(
                status_code=500,
                detail="Organizer profile creation failed and cleanup needs administrator attention",
            ) from None
        if exc.code == "23505":
            detail = "Username is already in use" if _is_username_conflict(exc) else "Witch assignment or organizer profile already exists"
            raise HTTPException(status_code=409, detail=detail) from None
        if exc.code == "23503":
            raise HTTPException(status_code=404, detail="Witch not found") from None
        if exc.code == "23502" and payload.witch_id is None:
            raise HTTPException(status_code=409, detail="The database requires every organizer to have a Witch assignment") from None
        if exc.code == "23502":
            logger.error(
                "Organizer profile insert rejected a required column (code=%s, message=%s, details=%s)",
                exc.code,
                getattr(exc, "message", ""),
                getattr(exc, "details", ""),
            )
            raise HTTPException(
                status_code=500,
                detail="The database rejected a required organizer profile field. Username and name were submitted; check the deployed API version and database triggers.",
            ) from None
        logger.error("Organizer profile creation failed (code=%s)", exc.code)
        raise HTTPException(status_code=500, detail="Organizer profile could not be created") from None
    except Exception:
        if not _cleanup_failed_organizer_creation(client, organizer_id):
            raise HTTPException(
                status_code=500,
                detail="Organizer profile creation failed and cleanup needs administrator attention",
            ) from None
        logger.error("Organizer profile insert failed unexpectedly; Auth account cleanup completed")
        raise HTTPException(status_code=503, detail="Organizer profile could not be created; the Auth account was cleaned up") from None
    return _response(client, created[0], auth_user, labels)


@router.patch("/{organizer_uuid}", response_model=OrganizerResponse)
def update_organizer(
    organizer_uuid: UUID,
    payload: OrganizerPatch,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    client = get_supabase()
    current = _read_profile(client, organizer_uuid)
    auth_user = _get_auth_user(client, str(organizer_uuid))
    changes = payload.model_dump(exclude_unset=True)
    desired_active = changes.get("is_active", current.get("is_active", False))
    desired_witch_id = changes.get("witch_id", current.get("witch_id"))
    if desired_active and not desired_witch_id:
        raise HTTPException(status_code=409, detail="An active organizer must be assigned to a Witch")
    if desired_active:
        _ensure_active_organizer_capacity(client, organizer_id=str(organizer_uuid))
    if "username" in changes:
        _ensure_username_available(client, changes["username"], exclude_id=str(organizer_uuid))
    if changes.get("witch_id") is not None:
        _ensure_witch_available(client, changes["witch_id"], organizer_id=str(organizer_uuid))

    profile_changes = {key: value for key, value in changes.items() if key in {"username", "name", "is_active", "witch_id"}}
    if "witch_id" in profile_changes and profile_changes["witch_id"] is not None:
        profile_changes["witch_id"] = str(profile_changes["witch_id"])
    if profile_changes:
        try:
            updated = (
                client.table("organizers")
                .update(profile_changes)
                .eq("id", str(organizer_uuid))
                .select(PROFILE_COLUMNS)
                .execute()
                .data
                or []
            )
        except APIError as exc:
            if exc.code == "23505":
                detail = (
                    "Username is already in use"
                    if _is_username_conflict(exc)
                    else "This Witch is already assigned to another organizer"
                )
                raise HTTPException(status_code=409, detail=detail) from None
            if exc.code == "23503":
                raise HTTPException(status_code=404, detail="Witch not found") from None
            if exc.code == "23502" and profile_changes.get("witch_id") is None:
                raise HTTPException(status_code=409, detail="The database requires every organizer to have a Witch assignment") from None
            logger.error("Organizer profile update failed (code=%s)", exc.code)
            raise HTTPException(status_code=500, detail="Organizer profile could not be updated") from None
        if not updated:
            raise HTTPException(status_code=404, detail="Organizer not found")
        current = updated[0]

    return _response(client, current, auth_user)


@router.put("/{organizer_uuid}/witch-assignment", response_model=OrganizerResponse)
def set_witch_assignment(
    organizer_uuid: UUID,
    payload: OrganizerAssignmentUpdate,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    current = _read_profile(get_supabase(), organizer_uuid)
    if current.get("is_active") is True and payload.witch_id is None:
        raise HTTPException(status_code=409, detail="An active organizer must remain assigned to a Witch")
    return update_organizer(
        organizer_uuid,
        OrganizerPatch(witch_id=payload.witch_id),
        admin,
    )


@router.post(
    "/{organizer_uuid}/reset-password",
    response_model=OrganizerPasswordResetResponse,
)
def reset_organizer_password(
    organizer_uuid: UUID,
    payload: OrganizerPasswordResetRequest = OrganizerPasswordResetRequest(),
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    client = get_supabase()
    _read_profile(client, organizer_uuid)
    temporary_password = secrets.token_urlsafe(18) if payload.password is None else None
    password = temporary_password or payload.password.get_secret_value()
    try:
        client.auth.admin.update_user_by_id(str(organizer_uuid), {"password": password})
    except Exception as exc:
        raise _auth_failure(exc, "reset password") from None
    return {"organizer_id": str(organizer_uuid), "temporary_password": temporary_password}


@router.delete("/{organizer_uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_organizer(organizer_uuid: UUID, admin: CurrentUser = Depends(require_admin)) -> None:
    del admin
    client = get_supabase()
    profile = _read_profile(client, organizer_uuid)
    try:
        deleted = (
            client.table("organizers")
            .delete()
            .eq("id", str(organizer_uuid))
            .select(PROFILE_COLUMNS)
            .execute()
            .data
            or []
        )
    except APIError as exc:
        logger.error("Organizer profile deletion failed (code=%s)", exc.code)
        raise HTTPException(status_code=500, detail="Organizer profile could not be deleted") from None
    if not deleted:
        raise HTTPException(status_code=404, detail="Organizer not found")
    try:
        client.auth.admin.delete_user(str(organizer_uuid))
    except Exception as exc:
        try:
            client.auth.admin.get_user_by_id(str(organizer_uuid))
        except Exception as verify_exc:
            if getattr(verify_exc, "status", None) == 404 or getattr(verify_exc, "status_code", None) == 404:
                return None
        try:
            client.table("organizers").insert(profile).execute()
        except Exception:
            logger.error("Organizer profile restoration failed after Auth deletion error")
        raise _auth_failure(exc, "delete user") from None
