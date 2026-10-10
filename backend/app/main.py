import logging

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.auth.routes import router as auth_router
from app.admin.game_config import router as admin_game_config_router
from app.admin.teams import router as admin_teams_router
from app.admin.witches import router as admin_witches_router
from app.admin.organizers import router as admin_organizers_router
from app.organizer.gameplay import router as organizer_gameplay_router
from app.config import Settings, get_settings
from app.db.supabase import get_supabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _check_database_connection() -> None:
    # Read at most one row to verify PostgREST/Postgres connectivity. The row is discarded.
    get_supabase().table("game_state").select("*").limit(1).execute()


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    api = FastAPI(title="Magic Witch - The Elixir of Eternal Life API")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    api.include_router(auth_router)
    api.include_router(organizer_gameplay_router)
    api.include_router(admin_teams_router)
    api.include_router(admin_game_config_router)
    api.include_router(admin_witches_router)
    api.include_router(admin_organizers_router)

    if app_settings.app_env.lower() == "development":
        from app.auth.dev_routes import router as dev_auth_router

        api.include_router(dev_auth_router)

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/health/db")
    async def database_health() -> dict[str, str]:
        try:
            await run_in_threadpool(_check_database_connection)
        except Exception:
            logger.exception("Supabase database health check failed")
            raise HTTPException(
                status_code=503,
                detail="Database connection unavailable",
            ) from None

        return {"status": "ok", "database": "connected"}

    return api


app = create_app()
