import logging
import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from postgrest.exceptions import APIError

logger = logging.getLogger(__name__)

WITCH_COUNT = 3
REAL_WITCH_COUNT = 2
FAKE_WITCH_COUNT = 1

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password(?:_hash)?|access_token|refresh_token|token|secret|authorization)\b"
    r"\s*[:=]\s*(?:'[^']*'|\"[^\"]*\"|[^\s,;]+)"
)
_SECRET_KEY_DETAIL = re.compile(
    r"(?i)(Key\s*\(\s*(?:password(?:_hash)?|access_token|refresh_token|token|secret|authorization)"
    r"\s*\)\s*=\s*\()[^)]*(\))"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def _safe_database_diagnostic(value: Any) -> str:
    """Keep useful PostgreSQL diagnostics while avoiding credential-like values."""
    message = str(value or "")
    message = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", message)
    message = _SECRET_KEY_DETAIL.sub(r"\1<redacted>\2", message)
    message = _JWT.sub("<redacted-token>", message)
    return message[:1000]


def database_error(operation: str, exc: APIError) -> HTTPException:
    if exc.code == "P0001":
        database_message = str(getattr(exc, "message", "")).lower()
        if "ingredient is referenced by the master order" in database_message:
            return HTTPException(status_code=409, detail="Ingredient is used by the master order")
        if "master order must contain" in database_message:
            return HTTPException(status_code=422, detail="Master order must contain 10 distinct active ingredients")
        if operation == "delete Witch":
            if "assigned to an organizer" in database_message:
                detail = "Witch is assigned to an organizer and cannot be deleted"
            elif "attempt history" in database_message:
                detail = "Witch has attempt history and cannot be deleted"
            elif "winner history" in database_message:
                detail = "Witch has winner history and cannot be deleted"
            elif "three-witch roster" in database_message:
                detail = "The three-Witch roster is fixed; deletion would leave too few Witches"
            else:
                detail = "Witch cannot be deleted in the current game state"
            return HTTPException(status_code=409, detail=detail)
        if "three-witch roster" in database_message:
            return HTTPException(status_code=409, detail="The roster allows exactly two REAL and one FAKE Witch identity")
        if "organizer profiles" in database_message:
            return HTTPException(status_code=409, detail="The Witch Hunt supports exactly three organizer profiles")
        if "witch uuid may be assigned" in database_message:
            return HTTPException(status_code=409, detail="Each Witch UUID must be assigned to a different active organizer")
        if "active organizer must be assigned" in database_message:
            return HTTPException(status_code=409, detail="An active organizer must be assigned to an existing Witch UUID")
        return HTTPException(
            status_code=409,
            detail="The database rejected this configuration change",
        )
    logger.error(
        "Game configuration database operation %s failed (code=%s, message=%s, details=%s)",
        operation,
        exc.code,
        _safe_database_diagnostic(getattr(exc, "message", "")),
        _safe_database_diagnostic(getattr(exc, "details", "")),
    )
    return HTTPException(status_code=500, detail="Game configuration operation failed")


def load_game_state(client: Any) -> dict[str, Any]:
    try:
        result = (
            client.table("game_state")
            .select("id,master_order,updated_at")
            .eq("id", 1)
            .limit(1)
            .execute()
        )
    except APIError as exc:
        raise database_error("read game state", exc) from None
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=503, detail="Game configuration singleton is unavailable")
    return rows[0]


def history_counts(client: Any) -> tuple[int, int]:
    """Legacy Witch-admin edit guard; the ingredient checker does not use history."""
    try:
        attempts = client.table("attempts").select("id").execute().data or []
        winners = client.table("winners").select("id").execute().data or []
    except APIError as exc:
        raise database_error("read legacy game history", exc) from None
    return len(attempts), len(winners)


def assert_configuration_editable(client: Any) -> dict[str, Any]:
    state = load_game_state(client)
    attempts, winners = history_counts(client)
    if attempts or winners:
        raise HTTPException(
            status_code=409,
            detail="Game configuration cannot be changed after submissions or winners exist",
        )
    return state


def assert_ingredient_configuration_editable(client: Any) -> dict[str, Any]:
    """Load canonical ingredient config; legacy gameplay history does not lock it."""
    return load_game_state(client)


def active_ingredients(client: Any) -> dict[str, dict[str, Any]]:
    try:
        result = (
            client.table("ingredients")
            .select("id,name,emoji,is_active,created_at,updated_at")
            .eq("is_active", True)
            .execute()
        )
    except APIError as exc:
        raise database_error("read active ingredients", exc) from None
    return {str(row["id"]): row for row in result.data or []}


def validate_ingredient_order(order: list[UUID] | list[str], active: dict[str, Any]) -> list[str]:
    ids = [str(item) for item in order]
    if len(ids) != 10:
        raise HTTPException(status_code=422, detail="Ingredient order must contain exactly 10 IDs")
    if len(set(ids)) != 10:
        raise HTTPException(status_code=422, detail="Ingredient order must not contain duplicate IDs")
    if any(ingredient_id not in active for ingredient_id in ids):
        raise HTTPException(status_code=422, detail="Every ordered ingredient must exist and be active")
    return ids


def witch_counts(client: Any) -> tuple[list[dict[str, Any]], int, int]:
    try:
        result = client.table("witches").select("id,witch_type,display_order").execute()
    except APIError as exc:
        raise database_error("read Witch setup", exc) from None
    rows = result.data or []
    real_count = sum(row.get("witch_type") == "real" for row in rows)
    return rows, len(rows), real_count


def ensure_witch_capacity(client: Any, witch_type: str, exclude_id: str | None = None) -> None:
    try:
        query = client.table("witches").select("id,witch_type")
        if exclude_id:
            query = query.neq("id", exclude_id)
        rows = query.execute().data or []
    except APIError as exc:
        raise database_error("check Witch capacity", exc) from None
    if len(rows) + 1 > WITCH_COUNT:
        raise HTTPException(status_code=409, detail="The Witch roster is limited to three identities")
    real_count = sum(row.get("witch_type") == "real" for row in rows) + (witch_type == "real")
    fake_count = sum(row.get("witch_type") == "fake" for row in rows) + (witch_type == "fake")
    if real_count > REAL_WITCH_COUNT:
        raise HTTPException(status_code=409, detail="The roster allows exactly two REAL Witches")
    if fake_count > FAKE_WITCH_COUNT:
        raise HTTPException(status_code=409, detail="The roster allows exactly one FAKE Witch")


def ingredient_details_for_order(client: Any, order: list[str]) -> list[dict[str, Any]]:
    if not order:
        return []
    try:
        rows = client.table("ingredients").select("id,name,emoji,is_active").in_("id", order).execute().data or []
    except APIError as exc:
        raise database_error("read ordered ingredients", exc) from None
    by_id = {str(row["id"]): row for row in rows}
    return [
        {
            "id": ingredient_id,
            "name": by_id.get(ingredient_id, {}).get("name"),
            "emoji": by_id.get(ingredient_id, {}).get("emoji"),
            "is_active": by_id.get(ingredient_id, {}).get("is_active"),
        }
        for ingredient_id in order
    ]


def validate_readiness(client: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Report only the ingredient/master-order setup required by the checker."""
    active = active_ingredients(client)
    master = [str(value) for value in (state.get("master_order") or [])]
    issues: list[str] = []

    if len(active) != 10:
        issues.append(f"Exactly 10 active ingredients are required; found {len(active)}.")
    master_valid = (
        len(master) == 10
        and len(set(master)) == 10
        and set(master) == set(active)
    )
    if not master_valid:
        issues.append("Master order must contain each of the 10 active ingredients exactly once.")

    return {
        "is_ready": not issues,
        "issues": issues,
        "active_ingredient_count": len(active),
        "master_order_valid": master_valid,
    }
