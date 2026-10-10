import copy
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.admin.team_codes import verify_team_code
import app.admin.game_config as admin_game_config_routes
import app.admin.witches as admin_witch_routes
import app.organizer.gameplay as organizer_gameplay_routes
from app.auth.dependencies import CurrentUser, get_current_user, require_admin, require_organizer
from app.config import Settings
from app.main import create_app
import app.auth.dependencies as auth_dependencies
import app.auth.dev_routes as dev_auth_routes
import app.db.supabase as supabase_db
import app.admin.teams as admin_teams_routes
import app.admin.organizers as admin_organizers_routes

ADMIN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
INACTIVE_ADMIN_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ORGANIZER_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
INACTIVE_ORGANIZER_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
ORGANIZER_C_ID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
UNKNOWN_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
WITCH_A = "11111111-1111-4111-8111-111111111111"
WITCH_B = "22222222-2222-4222-8222-222222222222"
WITCH_C = "33333333-3333-4333-8333-333333333333"
INGREDIENT_IDS = [f"{value:08x}-3333-4333-8333-{value:012x}" for value in range(1, 11)]


class FakeAuthError(Exception):
    status = 401


class FakeAuthAdmin:
    def __init__(self):
        self.users = {}
        self.passwords = {}

    def list_users(self, page=1, per_page=50):
        values = list(self.users.values())
        start = (page - 1) * per_page
        return values[start : start + per_page]

    def get_user_by_id(self, user_id):
        user = self.users.get(str(user_id))
        if user is None:
            error = FakeAuthError("user not found")
            error.status = 404
            raise error
        return SimpleNamespace(user=user)

    def create_user(self, attributes):
        email = attributes["email"].lower()
        if any(user.email.lower() == email for user in self.users.values()):
            error = FakeAuthError("User already registered")
            error.status = 422
            error.code = "email_exists"
            raise error
        user_id = str(uuid4())
        user = SimpleNamespace(
            id=user_id,
            email=email,
            user_metadata=copy.deepcopy(attributes.get("user_metadata") or {}),
            app_metadata=copy.deepcopy(attributes.get("app_metadata") or {}),
        )
        self.users[user_id] = user
        self.passwords[user_id] = attributes["password"]
        return SimpleNamespace(user=user)

    def update_user_by_id(self, user_id, attributes):
        user = self.users.get(str(user_id))
        if user is None:
            error = FakeAuthError("user not found")
            error.status = 404
            raise error
        if "user_metadata" in attributes:
            user.user_metadata = copy.deepcopy(attributes["user_metadata"])
        if "app_metadata" in attributes:
            user.app_metadata = copy.deepcopy(attributes["app_metadata"])
        if "password" in attributes:
            self.passwords[str(user_id)] = attributes["password"]
        return SimpleNamespace(user=user)

    def delete_user(self, user_id):
        self.users.pop(str(user_id), None)
        self.passwords.pop(str(user_id), None)


class FakeQuery:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.columns = "*"
        self.filters = []
        self.max_rows = None
        self.range_bounds = None
        self.search_term = None
        self.orders = []
        self.count_requested = False
        self.operation = "select"
        self.values = {}

    def select(self, columns, count=None, head=None):
        self.columns = columns
        self.count_requested = count == "exact"
        self.database.selected_columns.append((self.table, columns))
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        self.database.filters.append((self.table, key, value))
        return self

    def in_(self, key, values):
        self.filters.append(("in", key, values))
        return self

    def ilike(self, key, pattern):
        self.filters.append(("ilike", key, pattern))
        return self

    def neq(self, key, value):
        self.filters.append(("neq", key, value))
        return self

    def gte(self, key, value):
        self.filters.append(("gte", key, value))
        return self

    def lte(self, key, value):
        self.filters.append(("lte", key, value))
        return self

    def or_(self, expression):
        match = re.search(r'ilike\."\*(.*?)\*"', expression)
        self.search_term = match.group(1).replace("\\_", "_").replace("\\%", "%") if match else ""
        return self

    def order(self, column, desc=False):
        self.orders.append((column, desc))
        return self

    def range(self, start, end):
        self.range_bounds = (start, end)
        return self

    def update(self, values):
        self.operation = "update"
        self.values = values
        return self

    def insert(self, values):
        self.operation = "insert"
        self.values = values
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def limit(self, count):
        self.max_rows = count
        return self

    def execute(self):
        self.database.query_counts[self.table] = self.database.query_counts.get(self.table, 0) + 1
        rows = list(self.database.tables.get(self.table, []))
        for operation, key, value in self.filters:
            if operation == "eq":
                rows = [row for row in rows if row.get(key) == value]
            elif operation == "neq":
                rows = [row for row in rows if row.get(key) != value]
            elif operation == "in":
                rows = [row for row in rows if row.get(key) in value]
            elif operation == "gte":
                rows = [row for row in rows if (row.get(key) or "") >= value]
            elif operation == "lte":
                rows = [row for row in rows if (row.get(key) or "") <= value]
            elif operation == "ilike":
                needle = value.strip("%").replace("\\%", "%").replace("\\_", "_").lower()
                rows = [row for row in rows if needle in str(row.get(key) or "").lower()]
        if self.search_term:
            needle = self.search_term.lower()
            rows = [
                row for row in rows
                if any(needle in str(row.get(key) or "").lower() for key in ("team_id", "team_name", "college_name"))
            ]
        for column, desc in reversed(self.orders):
            rows.sort(key=lambda row: row.get(column) or "", reverse=desc)
        total = len(rows)
        if self.range_bounds is not None:
            start, end = self.range_bounds
            rows = rows[start : end + 1]
        if self.max_rows is not None:
            rows = rows[: self.max_rows]
        if self.operation == "insert":
            unique_column = {"ingredients": "name", "witches": "witch_id", "organizers": "witch_id"}.get(self.table)
            if self.table == "organizers" and self.values.get("witch_id") is None:
                unique_column = None
            if self.table == "organizers" and any(
                row.get("id") == self.values.get("id") for row in self.database.tables[self.table]
            ):
                raise APIError({"code": "23505", "message": "duplicate organizer id"})
            if unique_column and any(row.get(unique_column) == self.values.get(unique_column) for row in self.database.tables[self.table]):
                raise APIError({"code": "23505", "message": "duplicate unique value"})
            now = datetime.now(timezone.utc).isoformat()
            inserted = {
                "id": str(uuid4()),
                "created_at": now,
                "updated_at": now,
                **({"is_active": True} if self.table == "ingredients" else {}),
                **({"is_locked": False} if self.table == "witches" else {}),
                **copy.deepcopy(self.values),
            }
            self.database.tables[self.table].append(inserted)
            rows = [inserted]
        elif self.operation == "update":
            for key in ("name", "witch_id"):
                if key in self.values and any(
                    row.get(key) == self.values[key] and row not in rows
                    for row in self.database.tables[self.table]
                ):
                    raise APIError({"code": "23505", "message": "duplicate unique value"})
            if self.table == "organizers" and self.values.get("witch_id") is not None and any(
                row.get("witch_id") == self.values["witch_id"] and row not in rows
                for row in self.database.tables[self.table]
            ):
                raise APIError({"code": "23505", "message": "duplicate Witch assignment"})
            for row in rows:
                row.update(self.values)
        elif self.operation == "delete":
            self.database.tables[self.table] = [
                row for row in self.database.tables[self.table] if row not in rows
            ]
        if self.columns != "*":
            fields = self.columns.split(",")
            rows = [{key: row[key] for key in fields if key in row} for row in rows]
        return SimpleNamespace(data=rows, count=total if self.count_requested else None)


class FakeRPC:
    def __init__(self, database, function, params):
        self.database = database
        self.function = function
        self.params = params

    def execute(self):
        if self.function == "admin_create_team_with_members":
            team = copy.deepcopy(self.params["p_team"])
            if any(row["team_id"] == team["team_id"] for row in self.database.tables["teams"]):
                raise APIError({"code": "23505", "message": "duplicate team id"})
            now = datetime.now(timezone.utc).isoformat()
            team.update(created_at=now, updated_at=now)
            self.database.tables["teams"].append(team)
            for member in self.params["p_members"]:
                self.database.tables["team_members"].append(
                    {"id": str(uuid4()), "team_id": team["team_id"], "created_at": now, **member}
                )
            return SimpleNamespace(data=None)

        if self.function == "admin_patch_team_with_members":
            params = self.params
            team = next((row for row in self.database.tables["teams"] if row["team_id"] == params["p_team_id"]), None)
            if team is None:
                return SimpleNamespace(data=False)
            team.update(params["p_changes"])
            team["updated_at"] = datetime.now(timezone.utc).isoformat()
            if params["p_replace_members"]:
                self.database.tables["team_members"] = [
                    row for row in self.database.tables["team_members"] if row["team_id"] != team["team_id"]
                ]
                for member in params["p_members"]:
                    self.database.tables["team_members"].append(
                        {"id": str(uuid4()), "team_id": team["team_id"], "created_at": team["updated_at"], **member}
                    )
            return SimpleNamespace(data=True)
        raise AssertionError(f"Unexpected RPC function: {self.function}")


class FakeSupabaseAuth:
    def __init__(self):
        self.sign_in_call_count = 0
        self.sign_in_exception = None
        self.admin = FakeAuthAdmin()
        self.users = {
            "admin-token": SimpleNamespace(id=ADMIN_ID, email="admin@example.test"),
            "inactive-admin-token": SimpleNamespace(id=INACTIVE_ADMIN_ID, email="inactive-admin@example.test"),
            "organizer-token": SimpleNamespace(id=ORGANIZER_ID, email="organizer@example.test"),
            "inactive-organizer-token": SimpleNamespace(id=INACTIVE_ORGANIZER_ID, email="inactive-org@example.test"),
            "organizer-c-token": SimpleNamespace(id=ORGANIZER_C_ID, email="organizer-c@example.test"),
            "unknown-token": SimpleNamespace(id=UNKNOWN_ID, email="unknown@example.test"),
        }

    def sign_in_with_password(self, credentials):
        self.sign_in_call_count += 1
        if self.sign_in_exception is not None:
            raise self.sign_in_exception
        valid_credentials = {
            ("admin@example.test", "local-admin-password"): "admin-token",
            ("organizer@example.test", "local-organizer-password"): "organizer-token",
        }
        token = valid_credentials.get((credentials.get("email"), credentials.get("password")))
        if token is None:
            managed = next(
                (
                    user for user_id, user in self.admin.users.items()
                    if user.email == credentials.get("email")
                    and self.admin.passwords.get(user_id) == credentials.get("password")
                ),
                None,
            )
            if managed is not None:
                token = f"managed-token-{managed.id}"
                self.users[token] = managed
        if token is None:
            raise FakeAuthError("invalid login credentials")
        return SimpleNamespace(
            session=SimpleNamespace(access_token=token, expires_in=3600, refresh_token="not-returned")
        )

    def get_user(self, token):
        user = self.users.get(token)
        if user is None:
            raise FakeAuthError("invalid token")
        return SimpleNamespace(user=user)


class FakeSupabase:
    def __init__(self):
        self.auth = FakeSupabaseAuth()
        self.selected_columns = []
        self.filters = []
        self.query_counts = {}
        self.rpc_calls = []
        self.tables = {
            "admin_users": [
                {"id": ADMIN_ID, "username": "main-admin", "is_active": True},
                {"id": INACTIVE_ADMIN_ID, "username": "inactive-admin", "is_active": False},
            ],
            "organizers": [
                {"id": ORGANIZER_ID, "username": "organizer-one", "name": "Organizer One", "witch_id": WITCH_A, "is_active": True},
                {"id": INACTIVE_ORGANIZER_ID, "username": "organizer-two", "name": "Inactive Organizer", "witch_id": WITCH_B, "is_active": False},
            ],
            "witches": [
                {
                    "id": WITCH_A,
                    "witch_id": "north",
                    "label": "North Hall",
                    "is_locked": False,
                    "witch_type": "real",
                    "password_hash": "physical-witch-secret",
                    "display_order": INGREDIENT_IDS.copy(),
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                },
                {
                    "id": WITCH_B,
                    "witch_id": "south",
                    "label": "South Hall",
                    "is_locked": True,
                    "witch_type": "fake",
                    "password_hash": "other-secret",
                    "display_order": INGREDIENT_IDS.copy(),
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "updated_at": "2026-01-02T00:00:00+00:00",
                },
            ],
            "ingredients": [
                {
                    "id": ingredient_id,
                    "name": f"Ingredient {index}",
                    "emoji": f"e{index}",
                    "is_active": True,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
                for index, ingredient_id in enumerate(INGREDIENT_IDS, start=1)
            ],
            "game_state": [
                {
                    "id": 1,
                    "status": "not_started",
                    "master_order": INGREDIENT_IDS.copy(),
                    "game_started_at": None,
                    "round_2_started_at": None,
                    "game_ended_at": None,
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "team_game_state": [],
            "attempts": [],
            "winners": [],
            "activity_logs": [],
            "teams": [
                {
                    "team_id": "MW-001",
                    "team_name": "Phoenix",
                    "college_name": "North College",
                    "department": "EEE",
                    "year": "3",
                    "email": "team@example.test",
                    "phone": "1234567890",
                    "emergency_contact": None,
                    "team_code_hash": "stored-hash-secret",
                    "is_active": True,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                },
                {
                    "team_id": "MW-002",
                    "team_name": "Phoenix",
                    "college_name": "South College",
                    "team_code_hash": "inactive-hash",
                    "is_active": False,
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "updated_at": "2026-01-02T00:00:00+00:00",
                },
            ],
            "team_members": [
                {
                    "id": str(uuid4()),
                    "team_id": "MW-001",
                    "member_number": 1,
                    "name": "Member One",
                    "roll_number": "R1",
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "registrations": [
                {
                    "id": str(uuid4()),
                    "team_id": "MW-001",
                    "submitted_at": "2026-01-01T00:00:00+00:00",
                    "transaction_id": "TX-1",
                    "payment_status": "pending",
                    "payment_screenshot": "receipt/path.png",
                    "whatsapp_status": "joined",
                    "consent": True,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
        self.auth.admin.users = {
            ADMIN_ID: SimpleNamespace(
                id=ADMIN_ID, email="admin@example.test", app_metadata={"username": "main-admin"}
            ),
            ORGANIZER_ID: SimpleNamespace(
                id=ORGANIZER_ID, email="organizer@example.test", app_metadata={"username": "organizer-one"}
            ),
            INACTIVE_ORGANIZER_ID: SimpleNamespace(
                id=INACTIVE_ORGANIZER_ID, email="inactive-org@example.test", app_metadata={"username": "organizer-two"}
            ),
            ORGANIZER_C_ID: SimpleNamespace(
                id=ORGANIZER_C_ID, email="organizer-c@example.test", app_metadata={"username": "organizer-three"}
            ),
        }

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, function, params):
        self.rpc_calls.append((function, copy.deepcopy(params)))
        return FakeRPC(self, function, params)


@pytest.fixture
def auth_client(monkeypatch):
    supabase = FakeSupabase()
    monkeypatch.setattr(auth_dependencies, "get_supabase", lambda: supabase)
    monkeypatch.setattr(admin_teams_routes, "get_supabase", lambda: supabase)
    monkeypatch.setattr(admin_game_config_routes, "get_supabase", lambda: supabase)
    monkeypatch.setattr(admin_witch_routes, "get_supabase", lambda: supabase)
    monkeypatch.setattr(admin_organizers_routes, "get_supabase", lambda: supabase)
    monkeypatch.setattr(organizer_gameplay_routes, "get_supabase", lambda: supabase)
    monkeypatch.setattr(dev_auth_routes, "create_supabase_auth_client", lambda: supabase)
    with TestClient(create_app(Settings(app_env="development"))) as client:
        yield client, supabase


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def admin_auth():
    return bearer("admin-token")


def organizer_auth():
    return bearer("organizer-token")


def make_game_ready(supabase):
    supabase.tables["game_state"][0]["master_order"] = INGREDIENT_IDS.copy()
    supabase.tables["witches"] = [
        {
            "id": WITCH_A if index == 0 else WITCH_B if index == 1 else WITCH_C,
            "witch_id": f"witch-{index}",
            "label": f"Hall {index}",
            "is_locked": False,
            "witch_type": "real" if index < 2 else "fake",
            "password_hash": f"hash-{index}",
            "display_order": INGREDIENT_IDS.copy(),
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        for index in range(3)
    ]
    supabase.tables["organizers"] = [
        {"id": ORGANIZER_ID, "username": "organizer-one", "name": "Organizer One", "witch_id": WITCH_A, "is_active": True},
        {"id": INACTIVE_ORGANIZER_ID, "username": "organizer-two", "name": "Organizer Two", "witch_id": WITCH_B, "is_active": True},
        {"id": ORGANIZER_C_ID, "username": "organizer-three", "name": "Organizer Three", "witch_id": WITCH_C, "is_active": True},
    ]


































































def test_admin_can_list_teams_with_members_and_count(auth_client):
    client, supabase = auth_client
    response = client.get("/admin/teams", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["team_id"] == "MW-001"
    assert body["items"][0]["members"][0]["name"] == "Member One"
    assert "team_code_hash" not in response.text
    assert supabase.query_counts["team_members"] == 1


def test_team_search_pagination_and_inactive_filter(auth_client):
    client, _ = auth_client
    response = client.get(
        "/admin/teams?search=South%20College&include_inactive=true&limit=1&offset=0",
        headers=admin_auth(),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["team_id"] == "MW-002"

    next_page = client.get("/admin/teams?include_inactive=true&limit=1&offset=1", headers=admin_auth())
    assert next_page.json()["items"][0]["team_id"] == "MW-002"


@pytest.mark.parametrize("headers,status_code", [(None, 401), (organizer_auth(), 403)])
def test_only_admin_can_list_teams(auth_client, headers, status_code):
    client, _ = auth_client
    response = client.get("/admin/teams", headers=headers)
    assert response.status_code == status_code


def test_team_detail_returns_members_and_registration_without_hash(auth_client):
    client, _ = auth_client
    response = client.get("/admin/teams/MW-001", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["members"][0]["roll_number"] == "R1"
    assert body["registration"]["payment_status"] == "pending"
    assert body["registration"]["transaction_id"] == "TX-1"
    assert "team_code_hash" not in response.text


def test_team_without_registration_does_not_get_fabricated_registration(auth_client):
    client, _ = auth_client
    response = client.get("/admin/teams/MW-002", headers=admin_auth())
    assert response.status_code == 200
    assert "registration" not in response.json()


def test_missing_team_returns_404(auth_client):
    client, _ = auth_client
    assert client.get("/admin/teams/MW-048", headers=admin_auth()).status_code == 404


def test_team_creation_hashes_code_and_creates_members_atomically(auth_client):
    client, supabase = auth_client
    code = "initial-team-code"
    response = client.post(
        "/admin/teams",
        headers=admin_auth(),
        json={
            "team_id": "MW-003",
            "team_name": "New Phoenix",
            "college_name": "North College",
            "team_code": code,
            "members": [{"name": "First"}, {"name": "Second", "roll_number": "R2"}],
        },
    )
    assert response.status_code == 201
    assert response.json()["team_id"] == "MW-003"
    assert len([row for row in supabase.tables["team_members"] if row["team_id"] == "MW-003"]) == 2
    stored_team = next(row for row in supabase.tables["teams"] if row["team_id"] == "MW-003")
    assert stored_team["team_code_hash"] != code
    assert verify_team_code(code, stored_team["team_code_hash"])
    assert code not in response.text
    assert "team_code_hash" not in response.text
    function_name, rpc_params = supabase.rpc_calls[-1]
    assert function_name == "admin_create_team_with_members"
    assert rpc_params["p_team"]["team_code_hash"] == stored_team["team_code_hash"]
    assert code not in repr(rpc_params)


def test_team_creation_validates_required_fields_and_member_count(auth_client):
    client, supabase = auth_client
    base = {"team_id": "MW-004", "team_name": "Four Members", "team_code": "some-code"}
    too_many = {**base, "members": [{"name": str(i)} for i in range(4)]}
    assert client.post("/admin/teams", headers=admin_auth(), json=too_many).status_code == 422
    missing_name = {"team_id": "MW-005", "team_code": "some-code"}
    assert client.post("/admin/teams", headers=admin_auth(), json=missing_name).status_code == 422
    assert not supabase.rpc_calls


def test_duplicate_team_id_returns_conflict(auth_client):
    client, _ = auth_client
    response = client.post(
        "/admin/teams",
        headers=admin_auth(),
        json={"team_id": "MW-001", "team_name": "Duplicate", "team_code": "some-code"},
    )
    assert response.status_code == 409


def test_patch_changes_profile_and_replaces_members_only(auth_client):
    client, supabase = auth_client
    old_hash = supabase.tables["teams"][0]["team_code_hash"]
    response = client.patch(
        "/admin/teams/MW-001",
        headers=admin_auth(),
        json={"team_name": "Updated Phoenix", "members": [{"name": "Replacement"}]},
    )
    assert response.status_code == 200
    assert response.json()["team_name"] == "Updated Phoenix"
    assert [member["name"] for member in response.json()["members"]] == ["Replacement"]
    assert supabase.tables["teams"][0]["team_code_hash"] == old_hash
    assert supabase.rpc_calls[-1][0] == "admin_patch_team_with_members"
    forbidden = client.patch("/admin/teams/MW-001", headers=admin_auth(), json={"team_id": "MW-099"})
    assert forbidden.status_code == 422
    assert client.patch("/admin/teams/MW-048", headers=admin_auth(), json={"team_name": "Missing"}).status_code == 404


def test_delete_soft_deactivates_team(auth_client):
    client, supabase = auth_client
    supabase.tables["game_state"][0]["status"] = "round_1_live"
    existing_state = {
        "team_id": "MW-001",
        "attempts_used": 2,
        "is_locked": True,
        "qualified": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    supabase.tables["team_game_state"].append(copy.deepcopy(existing_state))
    response = client.delete("/admin/teams/MW-001", headers=admin_auth())
    assert response.status_code == 200
    assert response.json() == {"team_id": "MW-001", "is_active": False}
    assert any(row["team_id"] == "MW-001" for row in supabase.tables["teams"])
    assert any(row["team_id"] == "MW-001" for row in supabase.tables["registrations"])
    assert supabase.tables["team_game_state"] == [existing_state]


def test_reset_code_returns_generated_code_once_and_stores_only_hash(auth_client):
    client, supabase = auth_client
    response = client.post("/admin/teams/MW-001/reset-code", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    stored_team = next(row for row in supabase.tables["teams"] if row["team_id"] == "MW-001")
    assert body["team_code"]
    assert verify_team_code(body["team_code"], stored_team["team_code_hash"])
    assert body["team_code"] not in client.get("/admin/teams/MW-001", headers=admin_auth()).text
    assert "team_code_hash" not in client.get("/admin/teams", headers=admin_auth()).text


def test_development_token_endpoint_returns_supabase_auth_token(auth_client):
    client, supabase = auth_client
    response = client.post(
        "/dev/auth/token",
        json={"email": "admin@example.test", "password": "local-admin-password"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "access_token": "admin-token",
        "token_type": "bearer",
        "expires_in": 3600,
    }
    assert supabase.auth.sign_in_call_count == 1


def test_development_login_is_absent_in_production():
    production_app = create_app(Settings(app_env="production"))
    with TestClient(production_app) as client:
        response = client.post(
            "/dev/auth/token",
            json={"email": "admin@example.test", "password": "local-admin-password"},
        )
    assert response.status_code == 404


def test_development_auth_client_does_not_persist_or_refresh_session(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        supabase_db,
        "get_settings",
        lambda: SimpleNamespace(supabase_url="https://example.test", supabase_service_role_key="server-key"),
    )

    def fake_create_client(url, key, options):
        captured.update(url=url, key=key, options=options)
        return object()

    monkeypatch.setattr(supabase_db, "create_client", fake_create_client)
    assert supabase_db.create_supabase_auth_client() is not None
    assert captured["options"].persist_session is False
    assert captured["options"].auto_refresh_token is False


def test_development_token_endpoint_rejects_invalid_credentials(auth_client):
    client, _ = auth_client
    response = client.post(
        "/dev/auth/token",
        json={"email": "admin@example.test", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_development_password_is_not_logged_or_persisted(auth_client, caplog):
    client, supabase = auth_client
    password = "local-admin-password"
    supabase.auth.sign_in_exception = RuntimeError(password)
    response = client.post(
        "/dev/auth/token",
        json={"email": "admin@example.test", "password": password},
    )
    assert response.status_code == 503
    assert password not in caplog.text
    assert password not in repr(supabase.tables)
    assert password not in response.text


def test_dev_token_works_with_existing_bearer_auth(auth_client):
    client, _ = auth_client
    login = client.post(
        "/dev/auth/token",
        json={"email": "admin@example.test", "password": "local-admin-password"},
    )
    token = login.json()["access_token"]
    me = client.get("/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["principal_type"] == "admin"


def test_swagger_openapi_includes_http_bearer_security(auth_client):
    client, _ = auth_client
    openapi = client.get("/openapi.json").json()
    assert openapi["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert {"HTTPBearer": []} in openapi["paths"]["/auth/me"]["get"]["security"]


def test_supabase_user_maps_to_admin_and_passes_admin_dependency(auth_client):
    client, _ = auth_client
    response = client.get("/auth/me", headers=bearer("admin-token"))
    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "user_id": ADMIN_ID,
        "email": "admin@example.test",
        "principal_type": "admin",
        "username": "main-admin",
    }
    user = CurrentUser(ADMIN_ID, "admin@example.test", "admin", username="main-admin")
    assert require_admin(user) is user


def test_supabase_user_maps_to_organizer(auth_client):
    client, supabase = auth_client
    response = client.get("/auth/me", headers=bearer("organizer-token"))
    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "user_id": ORGANIZER_ID,
        "email": "organizer@example.test",
        "principal_type": "organizer",
        "username": "organizer-one",
        "name": "Organizer One",
    }
    assert ("organizers", "id,username,name,is_active") in supabase.selected_columns


def test_authenticated_user_without_profile_gets_forbidden(auth_client):
    client, _ = auth_client
    assert client.get("/auth/me", headers=bearer("unknown-token")).status_code == 403


@pytest.mark.parametrize("token", ["inactive-admin-token", "inactive-organizer-token"])
def test_inactive_profile_gets_forbidden(auth_client, token):
    client, _ = auth_client
    assert client.get("/auth/me", headers=bearer(token)).status_code == 403


def test_organizer_passes_organizer_dependency_and_cannot_pass_admin_dependency(auth_client):
    _, _ = auth_client
    user = CurrentUser(ORGANIZER_ID, "organizer@example.test", "organizer", name="Organizer One")
    assert require_organizer(user) is user
    with pytest.raises(HTTPException) as error:
        require_admin(user)
    assert error.value.status_code == 403


def test_missing_or_invalid_bearer_token_gets_unauthorized(auth_client):
    client, _ = auth_client
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers=bearer("invalid-token")).status_code == 401


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_cors_preflight_allows_vite_frontend_origins(auth_client, origin):
    client, _ = auth_client
    response = client.options(
        "/auth/me",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "GET" in response.headers["access-control-allow-methods"]
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_cors_preflight_rejects_unconfigured_origin(auth_client):
    client, _ = auth_client
    response = client.options(
        "/auth/me",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 400
    assert response.text == "Disallowed CORS origin"
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_authorized_auth_me_request(auth_client):
    client, _ = auth_client
    response = client.get(
        "/auth/me",
        headers={**bearer("admin-token"), "Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200
    assert response.json()["principal_type"] == "admin"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_production_cors_requires_explicit_frontend_origins():
    assert Settings(app_env="production", frontend_origins="").allowed_frontend_origins == []
    assert Settings(
        app_env="production",
        frontend_origins="https://event.example",
    ).allowed_frontend_origins == ["https://event.example"]


def test_auth_me_returns_only_safe_principal_information(auth_client):
    client, _ = auth_client
    response = client.get("/auth/me", headers=bearer("organizer-token"))
    assert response.status_code == 200
    body = response.json()
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert "password_hash" not in body
    assert "witch_type" not in body
    assert "master_order" not in body
    assert "witch_id" not in body






def test_organizer_ingredient_options_are_active_and_safe(auth_client):
    client, supabase = auth_client
    supabase.tables["ingredients"][0]["is_active"] = False

    response = client.get("/organizer/ingredients", headers=organizer_auth())

    assert response.status_code == 200
    assert len(response.json()) == 9
    assert all(set(item) == {"id", "name", "emoji"} for item in response.json())
    assert "master_order" not in response.text
    assert "witch_type" not in response.text
    assert "password_hash" not in response.text
    assert ("ingredients", "is_active", True) in supabase.filters
    assert client.get("/organizer/ingredients", headers=admin_auth()).status_code == 403
    assert client.get("/organizer/ingredients").status_code == 401


def test_organizer_order_checker_returns_only_binary_result_and_writes_no_gameplay_records(auth_client):
    client, supabase = auth_client
    supabase.tables["game_state"][0]["master_order"] = INGREDIENT_IDS.copy()
    supabase.tables["game_state"][0]["status"] = "ended"
    before = {table: copy.deepcopy(supabase.tables[table]) for table in (
        "attempts", "activity_logs", "winners", "team_game_state",
    )}

    response = client.post(
        "/organizer/check-order",
        headers=organizer_auth(),
        json={"ingredient_ids": INGREDIENT_IDS},
    )

    assert response.status_code == 200
    assert response.json() == {"result": "CORRECT"}
    assert {table: supabase.tables[table] for table in before} == before
    assert supabase.rpc_calls == []
    assert "master_order" not in response.text
    assert "match_percentage" not in response.text


def test_organizer_order_checker_is_exact_position_only(auth_client):
    client, supabase = auth_client
    supabase.tables["game_state"][0]["master_order"] = INGREDIENT_IDS.copy()
    submitted = INGREDIENT_IDS.copy()
    submitted[0], submitted[1] = submitted[1], submitted[0]

    response = client.post(
        "/organizer/check-order",
        headers=organizer_auth(),
        json={"ingredient_ids": submitted},
    )

    assert response.status_code == 200
    assert response.json() == {"result": "INCORRECT"}


@pytest.mark.parametrize("order", [
    INGREDIENT_IDS[:-1],
    [*INGREDIENT_IDS[:-1], INGREDIENT_IDS[0]],
])
def test_organizer_order_checker_rejects_incomplete_or_duplicate_orders(auth_client, order):
    client, _ = auth_client
    response = client.post(
        "/organizer/check-order",
        headers=organizer_auth(),
        json={"ingredient_ids": order},
    )
    assert response.status_code == 422


def test_organizer_order_checker_rejects_inactive_or_unknown_ingredient(auth_client):
    client, supabase = auth_client
    supabase.tables["ingredients"][0]["is_active"] = False
    supabase.tables["ingredients"].append({"id": "99999999-9999-4999-8999-999999999999", "name": "Extra", "emoji": None, "is_active": True})
    response = client.post(
        "/organizer/check-order",
        headers=organizer_auth(),
        json={"ingredient_ids": INGREDIENT_IDS},
    )
    assert response.status_code == 422


def test_organizer_order_checker_requires_active_organizer_auth(auth_client):
    client, _ = auth_client
    assert client.post("/organizer/check-order", json={"ingredient_ids": INGREDIENT_IDS}).status_code == 401
    assert client.post(
        "/organizer/check-order", headers=admin_auth(), json={"ingredient_ids": INGREDIENT_IDS},
    ).status_code == 403


def test_organizer_lifecycle_status_endpoint_is_removed(auth_client):
    client, _ = auth_client
    assert client.get("/organizer/status", headers=organizer_auth()).status_code == 404
    assert "/organizer/status" not in client.get("/openapi.json").json()["paths"]


def test_organizer_team_and_witch_gameplay_endpoints_are_removed(auth_client):
    client, _ = auth_client
    paths = client.get("/openapi.json").json()["paths"]
    assert "/organizer/teams" not in paths
    assert "/organizer/attempts" not in paths
    assert "/organizer/witch" not in paths


def test_admin_cannot_use_organizer_dependency(auth_client):
    _, _ = auth_client
    user = CurrentUser(ADMIN_ID, "admin@example.test", "admin", username="main-admin")
    with pytest.raises(HTTPException) as error:
        require_organizer(user)
    assert error.value.status_code == 403


def test_direct_get_current_user_verifies_supabase_auth_user(auth_client):
    _, _ = auth_client
    credentials = SimpleNamespace(scheme="Bearer", credentials="admin-token")
    user = get_current_user(credentials)
    assert user.user_id == ADMIN_ID
    assert user.principal_type == "admin"


def test_admin_can_list_ingredients_and_organizers_cannot(auth_client):
    client, _ = auth_client
    response = client.get("/admin/ingredients", headers=admin_auth())
    assert response.status_code == 200
    assert len(response.json()) == 10
    assert "password_hash" not in response.text
    assert client.get("/admin/ingredients", headers=organizer_auth()).status_code == 403
    assert client.get("/admin/ingredients").status_code == 401


def test_ingredient_creation_duplicate_and_validation(auth_client):
    client, supabase = auth_client
    response = client.post("/admin/ingredients", headers=admin_auth(), json={"name": "Moon Salt", "emoji": "🌙"})
    assert response.status_code == 201
    assert response.json()["name"] == "Moon Salt"
    assert response.json()["is_active"] is True
    assert client.post("/admin/ingredients", headers=admin_auth(), json={"name": "Moon Salt"}).status_code == 409
    assert client.post("/admin/ingredients", headers=admin_auth(), json={"name": "  "}).status_code == 422
    assert len(supabase.tables["ingredients"]) == 11


def test_ingredient_update_duplicate_and_safe_soft_deactivation(auth_client):
    client, supabase = auth_client
    ingredient_id = INGREDIENT_IDS[0]
    duplicate = client.patch(
        f"/admin/ingredients/{ingredient_id}",
        headers=admin_auth(),
        json={"name": "Ingredient 2"},
    )
    assert duplicate.status_code == 409

    referenced = client.delete(f"/admin/ingredients/{ingredient_id}", headers=admin_auth())
    assert referenced.status_code == 409
    assert supabase.tables["ingredients"][0]["is_active"] is True

    supabase.tables["game_state"][0]["master_order"] = []
    for witch in supabase.tables["witches"]:
        witch["display_order"] = []
    changed = client.patch(
        f"/admin/ingredients/{ingredient_id}",
        headers=admin_auth(),
        json={"name": "Updated Ingredient"},
    )
    assert changed.status_code == 200
    deactivated = client.delete(f"/admin/ingredients/{ingredient_id}", headers=admin_auth())
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert any(row["id"] == ingredient_id for row in supabase.tables["ingredients"])
    assert len(client.get("/admin/ingredients", headers=admin_auth()).json()) == 9
    assert len(client.get("/admin/ingredients?include_inactive=true", headers=admin_auth()).json()) == 10
    assert client.patch(
        "/admin/ingredients/99999999-9999-4999-8999-999999999999",
        headers=admin_auth(),
        json={"is_active": False},
    ).status_code == 404


def test_master_order_requires_ten_distinct_active_ingredients(auth_client):
    client, supabase = auth_client
    saved_order = INGREDIENT_IDS[2:] + INGREDIENT_IDS[:2]
    response = client.put(
        "/admin/game-config/master-order",
        headers=admin_auth(),
        json={"ingredient_ids": saved_order},
    )
    assert response.status_code == 200
    assert response.json()["master_order"] == saved_order
    assert supabase.tables["game_state"][0]["master_order"] == saved_order
    checked = client.post("/organizer/check-order", headers=organizer_auth(), json={"ingredient_ids": saved_order})
    assert checked.status_code == 200
    assert set(checked.json()) == {"result"}

    assert client.put(
        "/admin/game-config/master-order",
        headers=admin_auth(),
        json={"ingredient_ids": INGREDIENT_IDS[:-1]},
    ).status_code == 422
    assert client.put(
        "/admin/game-config/master-order",
        headers=admin_auth(),
        json={"ingredient_ids": [*INGREDIENT_IDS[:-1], INGREDIENT_IDS[0]]},
    ).status_code == 422
    assert client.put(
        "/admin/game-config/master-order",
        headers=admin_auth(),
        json={"ingredient_ids": [*INGREDIENT_IDS[:-1], "99999999-9999-4999-8999-999999999999"]},
    ).status_code == 422

    supabase.tables["ingredients"][0]["is_active"] = False
    assert client.put(
        "/admin/game-config/master-order",
        headers=admin_auth(),
        json={"ingredient_ids": INGREDIENT_IDS},
    ).status_code == 422


def test_master_order_configuration_is_not_locked_by_legacy_gameplay_history(auth_client):
    client, supabase = auth_client
    supabase.tables["attempts"] = [{"id": "legacy-attempt"}]
    supabase.tables["winners"] = [{"team_id": "legacy-team"}]
    response = client.put(
        "/admin/game-config/master-order",
        headers=admin_auth(),
        json={"ingredient_ids": INGREDIENT_IDS},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("legacy_status", ["round_1_live", "waiting_for_clue", "round_2_live", "paused", "ended"])
def test_configuration_remains_editable_for_legacy_status_without_history(auth_client, legacy_status):
    client, supabase = auth_client
    supabase.tables["game_state"][0]["status"] = legacy_status
    response = client.put(
        "/admin/game-config/master-order",
        headers=admin_auth(),
        json={"ingredient_ids": INGREDIENT_IDS},
    )
    assert response.status_code == 200, response.text


def test_admin_can_list_witch_configuration_without_hashes(auth_client):
    client, supabase = auth_client
    response = client.get("/admin/witches", headers=admin_auth())
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["witch_type"] in {"real", "fake"}
    assert response.json()[0]["organizer_assignments"][0]["name"] == "Organizer One"
    assert "password_hash" not in response.text
    assert "physical-witch-secret" not in response.text
    assert ("witches", "id,witch_id,label,witch_type,display_order,is_locked,created_at,updated_at") in supabase.selected_columns
    assert client.get("/admin/witches", headers=organizer_auth()).status_code == 403
    assert client.get("/admin/witches").status_code == 401
    detail = client.get(f"/admin/witches/{WITCH_A}", headers=admin_auth())
    assert detail.status_code == 200
    assert detail.json()["display_order"] == INGREDIENT_IDS
    assert detail.json()["organizer_assignments"][0]["id"] == ORGANIZER_ID
    assert "password_hash" not in detail.text
    assert client.get(f"/admin/witches/{WITCH_A}", headers=organizer_auth()).status_code == 403
    assert client.get("/admin/witches/99999999-9999-4999-8999-999999999999", headers=admin_auth()).status_code == 404


def test_witch_creation_uses_supabase_auth_profiles_not_witch_passwords(auth_client):
    client, supabase = auth_client
    payload = {
        "witch_id": "west", "label": "West Hall", "witch_type": "real",
        "display_order": INGREDIENT_IDS,
    }
    response = client.post("/admin/witches", headers=admin_auth(), json=payload)
    assert response.status_code == 201
    assert response.json()["witch_id"] == "west"
    assert "password_hash" not in response.text
    stored = next(row for row in supabase.tables["witches"] if row["witch_id"] == "west")
    assert stored["password_hash"]
    assert client.post("/admin/witches", headers=admin_auth(), json=payload).status_code == 409
    assert client.post(
        "/admin/witches", headers=organizer_auth(), json={**payload, "witch_id": "east"}
    ).status_code == 403


def test_witch_creation_logs_database_diagnostics_without_secrets(auth_client, monkeypatch, caplog):
    client, _ = auth_client
    original_execute = FakeQuery.execute

    def execute_with_missing_column(self):
        if self.table == "witches" and self.operation == "insert":
            raise APIError(
                {
                    "code": "42703",
                    "message": 'column "stale_column" does not exist',
                    "details": (
                        "trigger function failed; password=witch-password token=eyJabcdefgh.abc.def; "
                        "Key (password_hash)=(private-hash-value) already exists"
                    ),
                }
            )
        return original_execute(self)

    monkeypatch.setattr(FakeQuery, "execute", execute_with_missing_column)
    response = client.post(
        "/admin/witches",
        headers=admin_auth(),
        json={
            "witch_id": "diagnostic",
            "label": "Diagnostic Witch",
            "witch_type": "real",
            "display_order": INGREDIENT_IDS,
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Game configuration operation failed"
    assert 'column "stale_column" does not exist' in caplog.text
    assert "trigger function failed" in caplog.text
    assert "password=<redacted>" in caplog.text
    assert "token=<redacted>" in caplog.text
    assert "witch-password" not in caplog.text
    assert "eyJabcdefgh.abc.def" not in caplog.text
    assert "private-hash-value" not in caplog.text


def test_witch_create_insert_and_response_columns_match_current_schema():
    # This contract mirrors the actual public.witches columns reported for the
    # deployed database. Keep it explicit so schema drift is caught in review.
    actual_columns = {
        "id", "witch_id", "password_hash", "label", "witch_type",
        "display_order", "is_locked", "created_at", "updated_at",
    }
    source = (Path(__file__).parents[1] / "app" / "admin" / "witches.py").read_text()
    assert 'WITCH_COLUMNS = "id,witch_id,label,witch_type,display_order,is_locked,created_at,updated_at"' in source
    for column in ("witch_id", "label", "witch_type", "password_hash", "display_order", "is_locked", "created_at", "updated_at"):
        assert f'"{column}"' in source
        assert column in actual_columns


def test_admin_organizer_list_and_create_keeps_auth_and_profile_in_sync(auth_client, caplog):
    caplog.set_level("INFO")
    client, supabase = auth_client
    extra_witch_id = str(uuid4())
    supabase.tables["witches"].append({
        "id": extra_witch_id, "witch_id": "spare", "label": "Spare Hall", "is_locked": False,
        "witch_type": "fake", "password_hash": "never-return-this", "display_order": INGREDIENT_IDS.copy(),
    })
    response = client.post(
        "/admin/organizers",
        headers=admin_auth(),
        json={
            "email": "new-organizer@example.test", "password": "initial-password",
            "username": "new-organizer", "name": "New Organizer", "witch_id": extra_witch_id,
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["email"] == "new-organizer@example.test"
    assert data["username"] == "new-organizer"
    assert data["witch_id"] == extra_witch_id
    assert data["witch_label"] == "Spare Hall"
    assert "password" not in response.text and "password_hash" not in response.text
    assert "username_nonblank=True" in caplog.text
    assert "name_nonblank=True" in caplog.text
    assert "Organizer Auth create succeeded" in caplog.text
    assert "columns=['id', 'is_active', 'name', 'username', 'witch_id']" in caplog.text
    assert "initial-password" not in caplog.text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in caplog.text
    organizer_id = data["id"]
    profile = next(row for row in supabase.tables["organizers"] if row["id"] == organizer_id)
    assert {key: profile[key] for key in ("id", "username", "name", "witch_id", "is_active")} == {
        "id": organizer_id,
        "username": "new-organizer",
        "name": "New Organizer",
        "witch_id": extra_witch_id,
        "is_active": True,
    }
    assert organizer_id in supabase.auth.admin.users
    assert supabase.auth.admin.passwords[organizer_id] == "initial-password"

    login = client.post(
        "/dev/auth/token",
        json={"email": "new-organizer@example.test", "password": "initial-password"},
    )
    assert login.status_code == 200, login.text
    authorized = client.get("/auth/me", headers=bearer(login.json()["access_token"]))
    assert authorized.status_code == 200
    assert authorized.json()["principal_type"] == "organizer"
    assert authorized.json()["principal_type"] == "organizer"
    assert "witch_id" not in authorized.json()

    listed = client.get("/admin/organizers", headers=admin_auth())
    assert listed.status_code == 200
    assert any(item["id"] == organizer_id for item in listed.json())
    assert client.get("/admin/organizers", headers=organizer_auth()).status_code == 403
    assert client.get("/admin/organizers").status_code == 401


def test_organizer_creation_rejects_missing_or_invalid_required_fields(auth_client):
    client, supabase = auth_client
    starting_user_ids = set(supabase.auth.admin.users)
    base = {
        "email": "required-fields@example.test",
        "password": "initial-password",
        "username": "required-fields",
        "name": "Required Fields",
        "witch_id": WITCH_A,
    }
    cases = [
        {key: value for key, value in base.items() if key != "username"},
        {key: value for key, value in base.items() if key != "name"},
        {**base, "username": "   "},
        {**base, "name": "   "},
    ]

    for payload in cases:
        response = client.post("/admin/organizers", headers=admin_auth(), json=payload)
        assert response.status_code == 422, response.text

    assert set(supabase.auth.admin.users) == starting_user_ids
    assert not any(row.get("email") == base["email"] for row in supabase.tables["organizers"])


def test_organizer_creation_detects_existing_auth_user_before_retry(auth_client, monkeypatch):
    client, supabase = auth_client
    orphan = supabase.auth.admin.create_user({
        "email": "existing-auth-user@example.test",
        "password": "existing-password",
    }).user

    def must_not_create(_attributes):
        raise AssertionError("pre-existing Auth email must be detected before create_user")

    monkeypatch.setattr(supabase.auth.admin, "create_user", must_not_create)
    response = client.post(
        "/admin/organizers",
        headers=admin_auth(),
        json={
            "email": orphan.email,
            "password": "never-return-this",
            "username": "existing-auth-user",
            "name": "Existing Auth User",
            "witch_id": WITCH_A,
        },
    )
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]
    assert orphan.id in supabase.auth.admin.users
    assert not any(row["id"] == orphan.id for row in supabase.tables["organizers"])


def test_organizer_creation_requires_witch_and_caps_profiles_at_three(auth_client):
    client, supabase = auth_client
    payload = {
        "email": "extra-organizer@example.test", "password": "fresh-password",
        "username": "extra-organizer", "name": "Extra Organizer",
    }
    missing_assignment = client.post("/admin/organizers", headers=admin_auth(), json=payload)
    assert missing_assignment.status_code == 422
    assert not any(user.email == payload["email"] for user in supabase.auth.admin.users.values())

    make_game_ready(supabase)
    over_capacity = client.post(
        "/admin/organizers", headers=admin_auth(), json={**payload, "witch_id": WITCH_A},
    )
    assert over_capacity.status_code == 409
    assert "three organizer profiles" in over_capacity.json()["detail"]
    assert len(supabase.tables["organizers"]) == 3
    assert not any(user.email == payload["email"] for user in supabase.auth.admin.users.values())


def test_organizer_duplicate_email_username_and_witch_are_rejected(auth_client):
    client, supabase = auth_client
    spare_witch_id = str(uuid4())
    supabase.tables["witches"].append({"id": spare_witch_id, "label": "Spare", "witch_id": "spare", "is_locked": False})
    base = {
        "email": "new-organizer@example.test", "password": "password",
        "username": "fresh-user", "name": "Fresh User", "witch_id": spare_witch_id,
    }
    assert client.post("/admin/organizers", headers=admin_auth(), json=base).status_code == 201
    duplicate_email = client.post(
        "/admin/organizers", headers=admin_auth(),
        json={**base, "username": "another-user", "witch_id": WITCH_A},
    )
    duplicate_username = client.post(
        "/admin/organizers", headers=admin_auth(),
        json={**base, "email": "another@example.test", "witch_id": WITCH_A},
    )
    duplicate_witch = client.post(
        "/admin/organizers", headers=admin_auth(),
        json={**base, "email": "third@example.test", "username": "third-user"},
    )
    assert duplicate_email.status_code == 409
    assert duplicate_username.status_code == 409
    assert duplicate_witch.status_code == 409
    assert len(supabase.tables["organizers"]) == 3
    migration = (Path(__file__).parents[1] / "sql" / "admin_organizer_management.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS username text" in migration
    assert "CREATE UNIQUE INDEX IF NOT EXISTS organizers_username_case_insensitive_key" in migration
    assert "lower(btrim(username))" in migration


def test_organizer_update_assignment_unassign_password_reset_and_delete(auth_client):
    client, supabase = auth_client
    spare_witch_id = str(uuid4())
    supabase.tables["witches"].append({"id": spare_witch_id, "label": "Spare", "witch_id": "spare", "is_locked": False})
    update = client.patch(
        f"/admin/organizers/{ORGANIZER_ID}", headers=admin_auth(),
        json={"username": "organizer-renamed", "name": "Renamed Organizer", "is_active": False},
    )
    assert update.status_code == 200, update.text
    assert update.json()["username"] == "organizer-renamed"
    assert update.json()["is_active"] is False
    assert next(row for row in supabase.tables["organizers"] if row["id"] == ORGANIZER_ID)["username"] == "organizer-renamed"

    assignment = client.put(
        f"/admin/organizers/{ORGANIZER_ID}/witch-assignment",
        headers=admin_auth(), json={"witch_id": spare_witch_id},
    )
    assert assignment.status_code == 200, assignment.text
    assert assignment.json()["witch_id"] == spare_witch_id
    unassigned = client.put(
        f"/admin/organizers/{ORGANIZER_ID}/witch-assignment",
        headers=admin_auth(), json={"witch_id": None},
    )
    assert unassigned.status_code == 422, unassigned.text

    reset = client.post(f"/admin/organizers/{ORGANIZER_ID}/reset-password", headers=admin_auth(), json={})
    assert reset.status_code == 200
    temporary_password = reset.json()["temporary_password"]
    assert temporary_password and temporary_password not in supabase.tables["organizers"].__repr__()
    assert supabase.auth.admin.passwords[ORGANIZER_ID] == temporary_password

    deleted = client.delete(f"/admin/organizers/{ORGANIZER_ID}", headers=admin_auth())
    assert deleted.status_code == 204
    assert not any(row["id"] == ORGANIZER_ID for row in supabase.tables["organizers"])
    assert ORGANIZER_ID not in supabase.auth.admin.users


def test_organizer_assignment_duplicate_and_missing_organizer_are_safe(auth_client):
    client, _ = auth_client
    duplicate = client.put(
        f"/admin/organizers/{ORGANIZER_ID}/witch-assignment",
        headers=admin_auth(), json={"witch_id": WITCH_B},
    )
    missing_witch = client.put(
        f"/admin/organizers/{ORGANIZER_ID}/witch-assignment",
        headers=admin_auth(), json={"witch_id": str(uuid4())},
    )
    missing_organizer = client.get(f"/admin/organizers/{uuid4()}", headers=admin_auth())
    assert duplicate.status_code == 409
    assert missing_witch.status_code == 404
    assert missing_organizer.status_code == 404


def test_organizer_creation_cleans_up_auth_user_when_profile_insert_fails(auth_client, monkeypatch):
    client, supabase = auth_client
    supabase.tables["witches"].append(
        {"id": WITCH_C, "witch_id": "fake-c", "label": "Shared Fake", "is_locked": False}
    )
    original_execute = FakeQuery.execute

    def reject_organizer_profile(self):
        if self.table == "organizers" and self.operation == "insert":
            raise APIError({"code": "23505", "message": "duplicate organizer profile"})
        return original_execute(self)

    monkeypatch.setattr(FakeQuery, "execute", reject_organizer_profile)
    response = client.post(
        "/admin/organizers", headers=admin_auth(),
        json={"email": "cleanup@example.test", "password": "never-log-this", "username": "cleanup-user", "name": "Cleanup", "witch_id": WITCH_C},
    )
    assert response.status_code == 409
    assert "never-log-this" not in response.text
    assert not any(user.email == "cleanup@example.test" for user in supabase.auth.admin.users.values())


def test_organizer_not_null_failure_confirms_payload_and_cleans_auth(auth_client, monkeypatch, caplog):
    client, supabase = auth_client
    spare_witch_id = str(uuid4())
    supabase.tables["witches"].append({
        "id": spare_witch_id, "witch_id": "not-null", "label": "Not Null Hall", "is_locked": False,
    })
    original_execute = FakeQuery.execute
    captured_payload = {}

    def reject_missing_profile_fields(self):
        if self.table == "organizers" and self.operation == "insert":
            captured_payload.update(copy.deepcopy(self.values))
            raise APIError({
                "code": "23502",
                "message": 'null value in column "username" of relation "organizers" violates not-null constraint',
                "details": "Failing row has username=NULL and name=NULL",
            })
        return original_execute(self)

    monkeypatch.setattr(FakeQuery, "execute", reject_missing_profile_fields)
    response = client.post(
        "/admin/organizers",
        headers=admin_auth(),
        json={
            "email": "not-null@example.test",
            "password": "private-profile-password",
            "username": "not-null-user",
            "name": "Not Null User",
            "witch_id": spare_witch_id,
        },
    )

    assert response.status_code == 500
    assert "Username and name were submitted" in response.json()["detail"]
    assert captured_payload["username"] == "not-null-user"
    assert captured_payload["name"] == "Not Null User"
    assert captured_payload["id"] not in supabase.auth.admin.users
    assert not any(user.email == "not-null@example.test" for user in supabase.auth.admin.users.values())
    assert "private-profile-password" not in response.text
    assert "private-profile-password" not in caplog.text


def test_organizer_delete_restores_profile_if_auth_delete_fails(auth_client, monkeypatch):
    client, supabase = auth_client

    def fail_delete(_user_id):
        raise RuntimeError("temporary Auth outage")

    monkeypatch.setattr(supabase.auth.admin, "delete_user", fail_delete)
    response = client.delete(f"/admin/organizers/{ORGANIZER_ID}", headers=admin_auth())
    assert response.status_code == 503
    assert any(row["id"] == ORGANIZER_ID for row in supabase.tables["organizers"])
    assert ORGANIZER_ID in supabase.auth.admin.users


def test_organizer_password_reset_reports_auth_failure(auth_client, monkeypatch):
    client, supabase = auth_client

    def fail_update(_user_id, _attributes):
        raise RuntimeError("Auth service unavailable")

    monkeypatch.setattr(supabase.auth.admin, "update_user_by_id", fail_update)
    response = client.post(
        f"/admin/organizers/{ORGANIZER_ID}/reset-password", headers=admin_auth(), json={}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Organizer authentication service is unavailable"


def test_configuration_trigger_scopes_record_fields_and_witch_insert(auth_client):
    client, _ = auth_client
    response = client.post(
        "/admin/witches",
        headers=admin_auth(),
        json={
            "witch_id": "trigger-regression",
            "label": "Trigger Regression",
            "witch_type": "real",
            "display_order": INGREDIENT_IDS,
        },
    )
    assert response.status_code == 201

    sql = (Path(__file__).parents[1] / "sql" / "admin_game_lifecycle_rpcs.sql").read_text()
    function = sql.split("CREATE OR REPLACE FUNCTION public.admin_guard_game_configuration_write()", 1)[1]
    function = function.split("$$;", 1)[0]
    game_state_branch = function.index("IF TG_TABLE_NAME = 'game_state' THEN")
    update_branch = function.index("IF TG_OP = 'UPDATE' THEN", game_state_branch)
    master_order_access = function.index("NEW.master_order", update_branch)
    game_state_branch_end = function.index("END IF;", master_order_access)
    assert game_state_branch < update_branch < master_order_access < game_state_branch_end
    assert "NEW.master_order" not in function[:game_state_branch]
    assert "IF TG_TABLE_NAME = 'witches' AND TG_OP = 'INSERT' THEN" in function
    assert "IF NEW.display_order IS NOT NULL" in function
    assert "NEW.witch_type = 'real'::public.witch_type" in function


def test_witch_create_requires_valid_display_order_and_label(auth_client):
    client, supabase = auth_client
    base = {"witch_id": "west", "label": "West Hall", "witch_type": "real"}
    missing_order = client.post("/admin/witches", headers=admin_auth(), json=base)
    invalid_order = client.post(
        "/admin/witches", headers=admin_auth(),
        json={**base, "display_order": [*INGREDIENT_IDS[:-1], INGREDIENT_IDS[0]]},
    )
    blank_label = client.post(
        "/admin/witches", headers=admin_auth(), json={**base, "label": " ", "display_order": INGREDIENT_IDS},
    )
    assert missing_order.status_code == 422
    assert invalid_order.status_code == 422
    assert blank_label.status_code == 422
    assert len(supabase.tables["witches"]) == 2


def test_witch_create_enforces_real_witch_and_account_caps(auth_client):
    client, supabase = auth_client
    supabase.tables["witches"].append({
        "id": WITCH_C, "witch_id": "fake-c", "label": None, "witch_type": "fake",
        "password_hash": "existing-hash", "display_order": INGREDIENT_IDS.copy(), "is_locked": False,
    })
    too_many = client.post(
        "/admin/witches",
        headers=admin_auth(),
        json={"witch_id": "fourth", "label": "Fourth", "witch_type": "fake", "display_order": INGREDIENT_IDS},
    )
    assert too_many.status_code == 409
    assert len(supabase.tables["witches"]) == 3

    supabase.tables["witches"] = supabase.tables["witches"][:2]
    third_real = client.post(
        "/admin/witches",
        headers=admin_auth(),
        json={"witch_id": "second-real", "label": "Second Real", "witch_type": "real", "display_order": INGREDIENT_IDS},
    )
    assert third_real.status_code == 201
    fourth_real = client.post(
        "/admin/witches",
        headers=admin_auth(),
        json={"witch_id": "third-real", "label": "Third Real", "witch_type": "real", "display_order": INGREDIENT_IDS},
    )
    assert fourth_real.status_code == 409


def test_witch_update_validates_configuration_without_credential_fields(auth_client):
    client, supabase = auth_client
    duplicate_order = [*INGREDIENT_IDS[:-1], INGREDIENT_IDS[0]]
    assert client.patch(
        f"/admin/witches/{WITCH_A}",
        headers=admin_auth(),
        json={"display_order": duplicate_order},
    ).status_code == 422
    existing_hash = next(row for row in supabase.tables["witches"] if row["id"] == WITCH_A)["password_hash"]
    response = client.patch(
        f"/admin/witches/{WITCH_A}",
        headers=admin_auth(),
        json={"witch_id": "north-updated", "display_order": INGREDIENT_IDS},
    )
    assert response.status_code == 200
    assert response.json()["witch_id"] == "north-updated"
    assert "password_hash" not in response.text
    stored = next(row for row in supabase.tables["witches"] if row["id"] == WITCH_A)
    assert stored["password_hash"] == existing_hash
    assert client.patch(f"/admin/witches/{WITCH_A}", headers=admin_auth(), json={"password": "legacy-secret"}).status_code == 422
    assert client.patch(
        "/admin/witches/99999999-9999-4999-8999-999999999999",
        headers=admin_auth(),
        json={"label": "Missing"},
    ).status_code == 404
    assert client.patch(
        f"/admin/witches/{WITCH_A}", headers=organizer_auth(), json={"label": "Forbidden"}
    ).status_code == 403


@pytest.mark.parametrize("reference", ["organizer", "attempt", "winner"])
def test_witch_delete_is_blocked_when_referenced(auth_client, reference):
    client, supabase = auth_client
    target_id = WITCH_A if reference == "organizer" else WITCH_B
    for index in range(7):
        supabase.tables["witches"].append(
            {
                "id": str(uuid4()), "witch_id": f"legacy-{index}", "label": f"Legacy {index}",
                "witch_type": "fake", "password_hash": "hash", "display_order": INGREDIENT_IDS.copy(),
                "is_locked": False,
            }
        )
    if reference != "organizer":
        supabase.tables["organizers"] = [
            row for row in supabase.tables["organizers"] if row["witch_id"] != target_id
        ]
    if reference == "attempt":
        supabase.tables["attempts"].append({"id": str(uuid4()), "witch_id": target_id})
    elif reference == "winner":
        supabase.tables["winners"].append({"position": 1, "witch_id": target_id})

    response = client.delete(f"/admin/witches/{target_id}", headers=admin_auth())

    assert response.status_code == 409
    assert {
        "organizer": "assigned to an organizer",
        "attempt": "attempt history",
        "winner": "winner history",
    }[reference] in response.json()["detail"]
    assert any(row["id"] == target_id for row in supabase.tables["witches"])


def test_witch_delete_only_removes_unreferenced_excess_legacy_record(auth_client):
    client, supabase = auth_client
    excess_id = str(uuid4())
    supabase.tables["witches"].extend(
        {
            "id": str(uuid4()), "witch_id": f"legacy-{index}", "label": f"Legacy {index}",
            "witch_type": "fake", "password_hash": "hash", "display_order": INGREDIENT_IDS.copy(),
            "is_locked": False,
        }
        for index in range(7)
    )
    supabase.tables["witches"].append(
        {
            "id": excess_id, "witch_id": "excess", "label": "Excess", "witch_type": "fake",
            "password_hash": "hash", "display_order": INGREDIENT_IDS.copy(), "is_locked": False,
        }
    )

    response = client.delete(f"/admin/witches/{excess_id}", headers=admin_auth())

    assert response.status_code == 200
    assert response.json() == {"id": excess_id, "witch_id": "excess", "deleted": True}
    assert len(supabase.tables["witches"]) == 9
    assert not any(row["id"] == excess_id for row in supabase.tables["witches"])
    assert client.delete(f"/admin/witches/{WITCH_B}", headers=admin_auth()).status_code == 409
    assert client.delete(f"/admin/witches/{WITCH_B}", headers=organizer_auth()).status_code == 403


def test_witch_delete_preserves_three_record_roster(auth_client):
    client, supabase = auth_client
    supabase.tables["organizers"] = []
    response = client.delete(f"/admin/witches/{WITCH_B}", headers=admin_auth())
    assert response.status_code == 409
    assert "three-Witch roster is fixed" in response.json()["detail"]
    assert len(supabase.tables["witches"]) == 2


def test_witch_delete_respects_configuration_lock_and_missing_records(auth_client):
    client, supabase = auth_client
    excess_id = str(uuid4())
    supabase.tables["witches"].extend(
        {
            "id": str(uuid4()), "witch_id": f"legacy-{index}", "label": f"Legacy {index}",
            "witch_type": "fake", "password_hash": "hash", "display_order": INGREDIENT_IDS.copy(),
            "is_locked": False,
        }
        for index in range(7)
    )
    supabase.tables["witches"].append(
        {
            "id": excess_id, "witch_id": "excess", "label": "Excess", "witch_type": "fake",
            "password_hash": "hash", "display_order": INGREDIENT_IDS.copy(), "is_locked": False,
        }
    )
    supabase.tables["game_state"][0]["status"] = "round_1_live"
    supabase.tables["attempts"].append({"id": str(uuid4()), "team_id": "MW-001"})

    response = client.delete(f"/admin/witches/{excess_id}", headers=admin_auth())
    missing = client.delete("/admin/witches/99999999-9999-4999-8999-999999999999", headers=admin_auth())

    assert response.status_code == 409
    assert "submissions or winners exist" in response.json()["detail"]
    assert missing.status_code == 404
    assert any(row["id"] == excess_id for row in supabase.tables["witches"])


def test_witch_lock_unlock_are_admin_only_and_setup_only(auth_client):
    client, supabase = auth_client
    locked = client.post(f"/admin/witches/{WITCH_A}/lock", headers=admin_auth())
    assert locked.status_code == 200
    assert locked.json()["is_locked"] is True
    unlocked = client.post(f"/admin/witches/{WITCH_A}/unlock", headers=admin_auth())
    assert unlocked.status_code == 200
    assert unlocked.json()["is_locked"] is False
    assert client.post(f"/admin/witches/{WITCH_A}/lock", headers=organizer_auth()).status_code == 403
    supabase.tables["game_state"][0]["status"] = "paused"
    supabase.tables["attempts"].append({"id": str(uuid4()), "team_id": "MW-001"})
    assert client.post(f"/admin/witches/{WITCH_A}/lock", headers=admin_auth()).status_code == 409


def test_legacy_witch_password_reset_is_not_exposed_as_authentication(auth_client):
    client, _ = auth_client
    response = client.post(f"/admin/witches/{WITCH_A}/reset-password", headers=admin_auth(), json={})
    assert response.status_code == 404
    assert f"/admin/witches/{{witch_uuid}}/reset-password" not in client.get("/openapi.json").json()["paths"]


def test_admin_game_config_reports_readiness_and_secret_fields_stay_private(auth_client):
    client, _ = auth_client
    response = client.get("/admin/game-config", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    assert "status" not in body
    assert "game_started_at" not in body
    assert body["readiness"]["is_ready"] is True
    assert set(body["readiness"]) == {"is_ready", "issues", "active_ingredient_count", "master_order_valid"}
    assert body["readiness"]["active_ingredient_count"] == 10
    assert body["readiness"]["master_order_valid"] is True


def test_readiness_is_true_for_complete_configuration(auth_client):
    client, supabase = auth_client
    make_game_ready(supabase)
    response = client.get("/admin/game-config", headers=admin_auth())
    assert response.status_code == 200
    assert response.json()["readiness"]["is_ready"] is True
    assert response.json()["readiness"] == {
        "is_ready": True,
        "issues": [],
        "active_ingredient_count": 10,
        "master_order_valid": True,
    }






@pytest.mark.parametrize(
    "path",
    ["/admin/game/start", "/admin/game/pause", "/admin/game/resume", "/admin/game/start-round-2", "/admin/game/end"],
)
def test_legacy_lifecycle_mutation_routes_are_removed(auth_client, path):
    client, _ = auth_client
    assert client.post(path, headers=admin_auth()).status_code == 404
    assert path not in client.get("/openapi.json").json()["paths"]




def test_checker_migration_retires_legacy_write_rpcs_without_deleting_history():
    sql = (Path(__file__).parents[1] / "sql" / "ingredient_order_checker.sql").read_text()
    assert "DROP FUNCTION IF EXISTS public.organizer_submit_attempt(uuid, text, uuid[])" in sql
    assert "DROP FUNCTION IF EXISTS public.organizer_get_team_submission_state(uuid, text)" in sql
    assert "DROP TABLE" not in sql.upper()
    assert "DELETE FROM public.attempts" not in sql
    assert "DELETE FROM public.winners" not in sql
    assert "DELETE FROM public.activity_logs" not in sql
    assert "INSERT INTO public.attempts" not in sql
    assert "INSERT INTO public.winners" not in sql
    assert "INSERT INTO public.activity_logs" not in sql
    assert "INSERT INTO public.team_game_state" not in sql
    assert "admin_guard_checker_configuration_write" in sql
    assert "NEW.master_order" in sql
    assert "OLD.display_order" not in sql
