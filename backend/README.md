# Magic Witch API

FastAPI backend for **Magic Witch – The Elixir of Eternal Life**. Supabase Auth owns email/password authentication and access-token lifecycle. FastAPI verifies bearer tokens and maps each Auth UUID to an active `admin_users` or `organizers` profile.

## Setup

From `backend/`, create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and configure the backend Supabase URL, service-role key, `APP_ENV`, and explicit `FRONTEND_ORIGINS`. Keep credentials private. The service-role key remains backend-only.

Provision one shared organizer Supabase Auth user for the physical organizers, plus an active `public.organizers` profile whose `id` matches `auth.users.id`. The current legacy database schema may still require a Witch UUID in `organizers.witch_id`; the checker authorization and submission paths do not read or use that assignment. Admins continue to authenticate through their active `admin_users` profile.

## Ingredient-order checker

The organizer workflow is intentionally stateless:

- `GET /organizer/ingredients` returns active ingredient UUIDs, names, and emojis.
- `POST /organizer/check-order` accepts `{ "ingredient_ids": [<10 UUIDs>] }` and returns only `{ "result": "CORRECT" }` or `{ "result": "INCORRECT" }`.

The request must contain exactly ten distinct UUIDs, and the submitted set must equal the ten active ingredients. The backend reads the configured `game_state.master_order` and compares every ingredient at its exact index. It never returns the answer order or a partial score. Checks do not write attempts, activity logs, winners, or team state. Only an authenticated active organizer can use these endpoints.

The UI is at `/organizer`; it lets the organizer reorder ingredients, check the order, and reset for another check. The common login uses Supabase Auth. Admins are routed to `/admin`, where the existing ingredient and master-order configuration tools remain available.

## Local API testing

Set `APP_ENV=development`, start the backend, and open `http://127.0.0.1:8000/docs`:

1. Call `POST /dev/auth/token` using a Supabase Auth user's email and password.
2. Copy `access_token`, click Swagger **Authorize**, and enter it as a Bearer token.
3. Call `/auth/me`, `/organizer/ingredients`, or `/organizer/check-order`.

The development helper uses Supabase Auth password sign-in and is not registered unless `APP_ENV=development`. Production clients sign in through Supabase Auth directly and send the resulting access token as `Authorization: Bearer <token>`.

## Admin configuration

All `/admin/ingredients` and `/admin/game-config` endpoints require an active admin profile. They support ingredient creation/editing/soft-deactivation, reading checker readiness, and saving the canonical master order. The server requires ten active ingredients and a valid master-order permutation. Existing ingredient UUIDs remain stable.

Legacy Witch, team, and organizer administration routes and historical tables may remain in this repository/database for compatibility and historical data. They are not used by the organizer checker UI or authorization path. Legacy attempt/winner records are not deleted or updated by the checker.

## Supabase SQL deployment

Apply [`sql/ingredient_order_checker.sql`](sql/ingredient_order_checker.sql) manually in Supabase SQL Editor during deployment. It installs checker-specific ingredient/master-order validation triggers, removes obsolete lifecycle/team-state triggers and lifecycle transition RPC, and drops the old `organizer_submit_attempt` and `organizer_get_team_submission_state` write RPCs. It does not drop tables, delete or reset historical rows, or insert gameplay records. The script is rerunnable. Verify it completes successfully; local tests do not validate the deployed PostgreSQL project. Do not apply the legacy `sql/witch_hunt_engine.sql` afterward; that would recreate the retired gameplay RPCs.

Deploy order:

1. Deploy the FastAPI backend with the old organizer attempt and lifecycle routes removed, and wait for old backend instances to drain.
2. Run `sql/ingredient_order_checker.sql` manually in the intended Supabase project.
3. Confirm Supabase accepted the migration.
4. Deploy the frontend.
5. Sign in using the shared organizer account and perform a correct and incorrect check. Confirm the response contains only the binary result.

The SQL does not remove `attempts`, `activity_logs`, `winners`, `team_game_state`, Witch, team, or organizer tables. No migration is needed for a frontend-only data reset; the checker stores no submission history.

## Run and test

From `backend/`:

```powershell
uvicorn app.main:app --reload
python -m pytest
```

The test suite uses mocked Supabase/Auth clients. It does not apply or validate SQL against PostgreSQL or connect to a live Supabase project.
