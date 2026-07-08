"""Mobius-user FastAPI app — runnable Cloud Run service.

Mounts the auth router at /api/v1/auth/*. Reads config from env:

  USER_DATABASE_URL                     Postgres connection string (required)
  JWT_SECRET                            Shared with consumers that validate access tokens
  JWT_ACCESS_TOKEN_EXPIRE_MINUTES       default 60
  JWT_REFRESH_TOKEN_EXPIRE_DAYS         default 7
  GOOGLE_CLIENT_ID                      Web-application OAuth client (optional; /google 503 without)
  MOBIUS_EMAIL_SKILL_URL                Optional — disables welcome emails when unset
  MOBIUS_WELCOME_EMAIL_DISABLED=1       Hard-off switch for the welcome email
  DEFAULT_TENANT_ID / DEFAULT_TENANT_NAME  Default tenant for unauthenticated callers
  CORS_ALLOW_ORIGINS                    Comma-separated; '*' allowed in dev only

Run locally:
  USER_DATABASE_URL=postgresql://... uvicorn app.main:app --port 8002
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# Load .env from repo root if present (dev convenience). In Cloud Run the
# environment is provided via --set-env-vars / --set-secrets so this is a no-op.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from mobius_user.db.session import init_db  # noqa: E402
from mobius_user.routes.admin import router as admin_router  # noqa: E402
from mobius_user.routes.fastapi_auth import router as auth_router  # noqa: E402
from mobius_user.routes.users import router as users_router  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="mobius-user", version="0.2.0")

# CORS — chat (and any other consumer hosted on a different origin) talks to
# this service via XHR. Defaults to '*' in dev for ergonomics; lock this down
# explicitly via CORS_ALLOW_ORIGINS in any non-dev environment.
_origins_env = (os.getenv("CORS_ALLOW_ORIGINS") or "*").strip()
allow_origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,  # we use Bearer tokens, not cookies
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


def _resolved_database_url() -> str:
    """Return USER_DATABASE_URL with ${DB_PASSWORD} template token substituted.

    Secret Manager mounts DB_PASSWORD as an env var (configured in deploy.sh).
    The deploy/dev.env URL contains the literal placeholder __DB_PASSWORD__ so
    the secret never lands in source. URL-encodes the password since Postgres
    URLs treat '$' specially.
    """
    from urllib.parse import quote

    url = os.getenv("USER_DATABASE_URL", "")
    pw = os.getenv("DB_PASSWORD", "")
    if "__DB_PASSWORD__" in url:
        url = url.replace("__DB_PASSWORD__", quote(pw, safe=""))
    return url


@app.on_event("startup")
def _startup() -> None:
    db_url = _resolved_database_url()
    if not db_url:
        logger.error("USER_DATABASE_URL is unset — DB-backed routes will fail")
        return
    init_db(db_url)
    logger.info(
        "mobius-user up · CORS=%s · GOOGLE_CLIENT_ID=%s · MOBIUS_EMAIL_SKILL_URL=%s",
        allow_origins,
        "set" if (os.getenv("GOOGLE_CLIENT_ID") or "").strip() else "unset",
        "set" if (os.getenv("MOBIUS_EMAIL_SKILL_URL") or "").strip() else "unset",
    )


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "mobius-user",
        "version": app.version,
        "google_sign_in": bool((os.getenv("GOOGLE_CLIENT_ID") or "").strip()),
        "welcome_email": bool((os.getenv("MOBIUS_EMAIL_SKILL_URL") or "").strip())
        and os.getenv("MOBIUS_WELCOME_EMAIL_DISABLED") != "1",
    }


@app.get("/api/v1/public-config")
def public_config() -> dict:
    """Public, unauthenticated config consumed by frontend on boot.

    Designed so a consumer (chat, extension) can fetch this once at startup
    and hand the Google client ID into the auth modal without baking it
    into bundles. Never put secrets here.
    """
    return {
        "google_client_id": (os.getenv("GOOGLE_CLIENT_ID") or "").strip() or None,
    }


app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(admin_router, prefix="/api/v1/admin")
app.include_router(users_router, prefix="/api/v1/users")


@app.get("/admin")
def admin_page():
    """Serve the static admin dashboard. Auth happens client-side via the page."""
    return FileResponse(Path(__file__).resolve().parent / "static" / "admin.html")
