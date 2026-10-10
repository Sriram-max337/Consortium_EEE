import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.exceptions import APIError

from app.admin.schemas import (
    RegistrationSummary,
    ResetTeamCodeResponse,
    TeamCreate,
    TeamDeleteResponse,
    TeamDetail,
    TeamListResponse,
    TeamMemberSummary,
    TeamPatch,
    TeamSummary,
)
from app.admin.team_codes import hash_team_code
from app.auth.dependencies import CurrentUser, require_admin
from app.db.supabase import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/teams", tags=["admin teams"])

TEAM_COLUMNS = (
    "team_id,team_name,college_name,department,year,email,phone,"
    "emergency_contact,is_active,created_at,updated_at"
)
MEMBER_COLUMNS = "team_id,member_number,name,roll_number,created_at"
REGISTRATION_COLUMNS = (
    "submitted_at,transaction_id,payment_status,payment_screenshot,"
    "whatsapp_status,consent,created_at,updated_at"
)
EDITABLE_FIELDS = (
    "team_name",
    "college_name",
    "department",
    "year",
    "email",
    "phone",
    "emergency_contact",
)


def _database_error(operation: str, exc: APIError) -> HTTPException:
    logger.error("Team database operation %s failed (code=%s)", operation, exc.code)
    return HTTPException(status_code=500, detail="Team database operation failed")


def _escape_filter_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "\\%").replace("_", "\\_")


def _member_summaries(rows: list[dict[str, Any]]) -> list[TeamMemberSummary]:
    return [
        TeamMemberSummary(
            member_number=row["member_number"],
            name=row.get("name"),
            roll_number=row.get("roll_number"),
            created_at=row.get("created_at"),
        )
        for row in rows
    ]


def _load_team(team_id: str) -> dict[str, Any]:
    client = get_supabase()
    try:
        team_result = client.table("teams").select(TEAM_COLUMNS).eq("team_id", team_id).limit(1).execute()
        teams = team_result.data or []
    except APIError as exc:
        raise _database_error("read team", exc) from None

    if not teams:
        raise HTTPException(status_code=404, detail="Team not found")

    try:
        member_result = (
            client.table("team_members")
            .select(MEMBER_COLUMNS)
            .eq("team_id", team_id)
            .order("member_number")
            .execute()
        )
        registration_result = (
            client.table("registrations")
            .select(REGISTRATION_COLUMNS)
            .eq("team_id", team_id)
            .limit(1)
            .execute()
        )
    except APIError as exc:
        raise _database_error("read team details", exc) from None

    registration_rows = registration_result.data or []
    result = dict(teams[0])
    result["members"] = _member_summaries(member_result.data or [])
    result["registration"] = RegistrationSummary(**registration_rows[0]) if registration_rows else None
    return result


@router.get("", response_model=TeamListResponse)
def list_teams(
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_inactive: bool = False,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    client = get_supabase()
    query = client.table("teams").select(TEAM_COLUMNS, count="exact")
    if not include_inactive:
        query = query.eq("is_active", True)
    if search and search.strip():
        pattern = f"*{_escape_filter_text(search.strip())}*"
        query = query.or_(
            f'team_id.ilike."{pattern}",team_name.ilike."{pattern}",college_name.ilike."{pattern}"'
        )

    try:
        response = query.order("team_id").range(offset, offset + limit - 1).execute()
        teams = response.data or []
        if teams:
            team_ids = [team["team_id"] for team in teams]
            member_response = (
                client.table("team_members")
                .select(MEMBER_COLUMNS)
                .in_("team_id", team_ids)
                .order("member_number")
                .execute()
            )
            members_by_team: dict[str, list[dict[str, Any]]] = {team_id: [] for team_id in team_ids}
            for member in member_response.data or []:
                members_by_team.setdefault(member["team_id"], []).append(member)
        else:
            members_by_team = {}
    except APIError as exc:
        raise _database_error("list teams", exc) from None

    items = []
    for team in teams:
        safe_team = dict(team)
        safe_team["members"] = _member_summaries(members_by_team.get(team["team_id"], []))
        items.append(TeamSummary(**safe_team))

    return {"items": items, "total": response.count or 0, "limit": limit, "offset": offset}


@router.get("/{team_id}", response_model=TeamDetail, response_model_exclude_none=True)
def get_team(team_id: str, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    del admin
    return _load_team(team_id)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TeamDetail, response_model_exclude_none=True)
def create_team(payload: TeamCreate, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    del admin
    team = {field: getattr(payload, field) for field in EDITABLE_FIELDS if field in payload.model_fields_set}
    team.update(
        {
            "team_id": payload.team_id,
            "team_name": payload.team_name,
            "team_code_hash": hash_team_code(payload.team_code.get_secret_value()),
            "is_active": True,
        }
    )
    members = [
        {"member_number": index, **member.model_dump()}
        for index, member in enumerate(payload.members, start=1)
    ]
    try:
        get_supabase().rpc(
            "admin_create_team_with_members",
            {"p_team": team, "p_members": members},
        ).execute()
    except APIError as exc:
        if exc.code == "23505":
            raise HTTPException(status_code=409, detail="Team ID already exists") from None
        raise _database_error("create team", exc) from None

    return _load_team(payload.team_id)


@router.patch("/{team_id}", response_model=TeamDetail, response_model_exclude_none=True)
def patch_team(
    team_id: str,
    payload: TeamPatch,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    changes = payload.model_dump(exclude_unset=True)
    members_were_supplied = "members" in changes
    members = changes.pop("members", []) or []
    try:
        result = get_supabase().rpc(
            "admin_patch_team_with_members",
            {
                "p_team_id": team_id,
                "p_changes": changes,
                "p_replace_members": members_were_supplied,
                "p_members": [
                    {"member_number": index, **member}
                    for index, member in enumerate(members, start=1)
                ],
            },
        ).execute()
    except APIError as exc:
        raise _database_error("update team", exc) from None

    if result.data is not True:
        raise HTTPException(status_code=404, detail="Team not found")
    return _load_team(team_id)


@router.delete("/{team_id}", response_model=TeamDeleteResponse)
def delete_team(team_id: str, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    del admin
    try:
        result = (
            get_supabase()
            .table("teams")
            .update({"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("team_id", team_id)
            .select("team_id")
            .execute()
        )
    except APIError as exc:
        raise _database_error("deactivate team", exc) from None
    if not result.data:
        raise HTTPException(status_code=404, detail="Team not found")
    return {"team_id": team_id, "is_active": False}


@router.post("/{team_id}/reset-code", response_model=ResetTeamCodeResponse)
def reset_team_code(team_id: str, admin: CurrentUser = Depends(require_admin)) -> dict[str, str]:
    del admin
    new_code = secrets.token_urlsafe(9)
    new_hash = hash_team_code(new_code)
    try:
        result = (
            get_supabase()
            .table("teams")
            .update(
                {
                    "team_code_hash": new_hash,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("team_id", team_id)
            .select("team_id")
            .execute()
        )
    except APIError as exc:
        raise _database_error("reset team code", exc) from None
    if not result.data:
        raise HTTPException(status_code=404, detail="Team not found")
    return {"team_id": team_id, "team_code": new_code}
