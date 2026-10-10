from functools import lru_cache

from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from app.config import get_settings


@lru_cache

def get_supabase() -> Client:
    """Return the reusable server-side Supabase client."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("Supabase configuration is missing")

    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def create_supabase_auth_client() -> Client:
    """Create a short-lived isolated Auth client for the development sign-in helper.

    Password sign-in mutates Supabase client's in-memory Auth state. A one-off
    client prevents it from affecting the reusable data client or another login.
    Session persistence and automatic refresh are disabled.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("Supabase configuration is missing")

    options = SyncClientOptions(auto_refresh_token=False, persist_session=False)
    return create_client(settings.supabase_url, settings.supabase_service_role_key, options=options)
