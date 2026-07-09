"""
FastAPI router for auth routes.

Mount with: app.include_router(fastapi_auth_router, prefix="/api/v1/auth")
Requires: fastapi (pip install mobius-user[fastapi])
"""

import os
import uuid
from datetime import datetime
from typing import Any, Optional

import logging

from mobius_user.services.auth_service import get_auth_service, get_user_from_token
from mobius_user.services.welcome_email import send_welcome_email
from mobius_user.db.session import get_db_session
from mobius_user.models.tenant import AppUser, UserOrgMembership
from mobius_user.models.activity import Activity, UserActivity
from mobius_user.models.preference import UserPreference

logger = logging.getLogger(__name__)

try:
    from fastapi import APIRouter, Depends, HTTPException, Request
    from pydantic import BaseModel
except ImportError:
    raise ImportError(
        "FastAPI is required for fastapi_auth. Install with: pip install mobius-user[fastapi]"
    )


router = APIRouter(tags=["auth"])

DEFAULT_TENANT_ID = uuid.UUID(
    os.getenv("DEFAULT_TENANT_ID", "00000000-0000-0000-0000-000000000001")
)


# Pydantic models
class RegisterBody(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = None
    first_name: Optional[str] = None
    tenant_id: Optional[str] = None


class LoginBody(BaseModel):
    email: str
    password: str
    tenant_id: Optional[str] = None
    device_info: Optional[dict] = None


class RefreshBody(BaseModel):
    refresh_token: str


class LogoutBody(BaseModel):
    refresh_token: Optional[str] = None


class OnboardingBody(BaseModel):
    preferred_name: Optional[str] = None
    activities: Optional[list[str]] = None
    ai_experience_level: Optional[str] = "beginner"
    autonomy_routine_tasks: Optional[str] = "confirm_first"
    autonomy_sensitive_tasks: Optional[str] = "confirm_first"
    tone: Optional[str] = "professional"
    greeting_enabled: Optional[bool] = True
    timezone: Optional[str] = "America/New_York"


class CheckEmailBody(BaseModel):
    email: str
    tenant_id: Optional[str] = None


class PreferencesBody(BaseModel):
    preferred_name: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    tone: Optional[str] = None
    greeting_enabled: Optional[bool] = None
    autonomy_routine_tasks: Optional[str] = None
    autonomy_sensitive_tasks: Optional[str] = None
    activities: Optional[list[str]] = None


def _get_tenant_id(tenant_id_str: Optional[str]) -> uuid.UUID:
    if tenant_id_str:
        try:
            return uuid.UUID(tenant_id_str)
        except ValueError:
            pass
    return DEFAULT_TENANT_ID


async def _get_current_user(request: Request) -> AppUser:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = auth[7:]
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


@router.post("/register")
def register(body: RegisterBody):
    email = (body.email or "").strip().lower()
    password = body.password or ""
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    tenant_id = _get_tenant_id(body.tenant_id)
    auth_service = get_auth_service()
    auth_service.get_or_create_default_tenant()

    user, error = auth_service.register_user(
        tenant_id=tenant_id,
        email=email,
        password=password,
        display_name=(body.display_name or "").strip() or None,
        first_name=(body.first_name or "").strip() or None,
    )

    if error:
        raise HTTPException(status_code=400, detail=error)

    auth_response, _ = auth_service.authenticate_email(
        email=email, password=password, tenant_id=tenant_id
    )

    # Best-effort welcome email — never blocks signup on failure.
    try:
        send_welcome_email(
            user_id=str(user.user_id),
            email=user.email or email,
            first_name=user.first_name or (body.first_name or "").strip() or None,
        )
    except Exception:
        logger.warning("register: welcome email send raised", exc_info=True)

    return {
        "ok": True,
        "message": "Registration successful",
        "is_new_user": True,
        **auth_response,
    }


@router.post("/google")
def google_sign_in(body: dict):
    """Sign in (or auto-create on first time) with a Google ID token.

    Body: {"id_token": "<google id token>", "tenant_id": "<optional>", "device_info": {...}}
    """
    id_token_str = (body.get("id_token") or "").strip()
    if not id_token_str:
        raise HTTPException(status_code=400, detail="id_token is required")

    tenant_id = _get_tenant_id(body.get("tenant_id"))
    device_info = body.get("device_info")

    auth_service = get_auth_service()
    auth_service.get_or_create_default_tenant()

    auth_response, is_new_user, error = auth_service.authenticate_google(
        id_token_str=id_token_str,
        tenant_id=tenant_id,
        device_info=device_info,
    )
    if error:
        raise HTTPException(status_code=401, detail=error)

    if is_new_user:
        user_obj = (auth_response or {}).get("user") or {}
        try:
            send_welcome_email(
                user_id=str(user_obj.get("user_id") or ""),
                email=str(user_obj.get("email") or ""),
                first_name=user_obj.get("first_name"),
            )
        except Exception:
            logger.warning("google: welcome email send raised", exc_info=True)

    return {"ok": True, "is_new_user": is_new_user, **auth_response}


@router.post("/login")
def login(body: LoginBody):
    email = (body.email or "").strip().lower()
    password = body.password or ""
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")

    tenant_id = _get_tenant_id(body.tenant_id)
    auth_response, error = get_auth_service().authenticate_email(
        email=email,
        password=password,
        tenant_id=tenant_id,
        device_info=body.device_info,
    )

    if error:
        raise HTTPException(status_code=401, detail=error)

    return {"ok": True, **auth_response}


@router.post("/refresh")
def refresh(body: RefreshBody):
    if not body.refresh_token:
        raise HTTPException(status_code=400, detail="Refresh token is required")

    auth_response, error = get_auth_service().refresh_access_token(body.refresh_token)

    if error:
        raise HTTPException(status_code=401, detail=error)

    return {"ok": True, **auth_response}


@router.post("/logout")
def logout(body: LogoutBody = LogoutBody()):
    if body.refresh_token:
        get_auth_service().logout(body.refresh_token)
    return {"ok": True, "message": "Logged out"}


@router.get("/me")
def me(user: AppUser = Depends(_get_current_user)):
    activities = []
    profile_envelope = None
    auth_service = get_auth_service()

    with get_db_session() as session:
        user_activities = (
            session.query(UserActivity).filter(UserActivity.user_id == user.user_id).all()
        )
        for ua in user_activities:
            activity = (
                session.query(Activity).filter(Activity.activity_id == ua.activity_id).first()
            )
            if activity:
                activities.append({
                    "activity_code": activity.activity_code,
                    "label": activity.label,
                    "is_primary": ua.is_primary,
                })
        preference = (
            session.query(UserPreference)
            .filter(UserPreference.user_id == user.user_id)
            .first()
        )

        # Lazy-regenerate the profile envelope when stale. Pure-Python build,
        # no LLM — safe to do inline on a read.
        if preference is not None:
            from mobius_user.services.prompt_builder import CURRENT_TEMPLATE_VERSION
            stored = preference.profile_json
            stored_version = preference.profile_version
            if stored and stored_version == CURRENT_TEMPLATE_VERSION:
                profile_envelope = stored
            # Stale or missing → regenerate via the service helper (its own
            # session handles the write).
        if preference is None or profile_envelope is None:
            profile_envelope = auth_service.regenerate_user_profile(user.user_id)

        org_memberships = [
            {
                "org_slug": m.org_slug,
                "display_name": m.org_display_name or m.org_slug,
                "roles": list(m.roles or []),
            }
            for m in session.query(UserOrgMembership)
            .filter(UserOrgMembership.user_id == user.user_id)
            .all()
        ]

    return {
        "ok": True,
        "user": {
            "user_id": str(user.user_id),
            "tenant_id": str(user.tenant_id),
            "email": user.email,
            "display_name": user.display_name,
            "first_name": user.first_name,
            "preferred_name": user.preferred_name,
            "timezone": user.timezone,
            "locale": user.locale,
            "avatar_url": user.avatar_url,
            "is_onboarded": user.is_onboarded,
            # Canonical org context — org_slug from the master org registry
            # (provider-roster-credentialing). Flows to every consumer that
            # authenticates via /me.
            "org_memberships": org_memberships,
            "activities": activities,
            "preference": {
                "tone": preference.tone if preference else "professional",
                "greeting_enabled": preference.greeting_enabled if preference else True,
                "ai_experience_level": (
                    preference.ai_experience_level if preference else "beginner"
                ),
                "autonomy_routine_tasks": (
                    preference.autonomy_routine_tasks if preference else "confirm_first"
                ),
                "autonomy_sensitive_tasks": (
                    preference.autonomy_sensitive_tasks if preference else "confirm_first"
                ),
            }
            if preference
            else None,
            "profile": profile_envelope,
        },
    }


@router.put("/onboarding")
def complete_onboarding(body: OnboardingBody, user: AppUser = Depends(_get_current_user)):
    with get_db_session() as session:
        db_user = (
            session.query(AppUser).filter(AppUser.user_id == user.user_id).first()
        )
        if not db_user:
            raise HTTPException(status_code=404, detail="User not found")

        if body.preferred_name:
            db_user.preferred_name = body.preferred_name
        db_user.timezone = body.timezone or "America/New_York"
        db_user.onboarding_completed_at = datetime.utcnow()

        preference = (
            session.query(UserPreference).filter(UserPreference.user_id == user.user_id).first()
        )
        if not preference:
            preference = UserPreference(user_id=user.user_id)
            session.add(preference)

        preference.tone = body.tone or "professional"
        preference.greeting_enabled = body.greeting_enabled
        preference.ai_experience_level = body.ai_experience_level or "beginner"
        preference.autonomy_routine_tasks = body.autonomy_routine_tasks or "confirm_first"
        preference.autonomy_sensitive_tasks = body.autonomy_sensitive_tasks or "confirm_first"

        session.query(UserActivity).filter(UserActivity.user_id == user.user_id).delete()

        for i, code in enumerate(body.activities or []):
            activity = (
                session.query(Activity).filter(Activity.activity_code == code).first()
            )
            if activity:
                session.add(
                    UserActivity(
                        user_id=user.user_id,
                        activity_id=activity.activity_id,
                        is_primary=(i == 0),
                    )
                )

        session.commit()

    # Regenerate the profile envelope so consumers picking up /me right
    # after onboarding see fresh data without an extra round trip.
    profile = get_auth_service().regenerate_user_profile(user.user_id)
    return {"ok": True, "message": "Onboarding completed", "profile": profile}


@router.post("/check-email")
def check_email(body: CheckEmailBody):
    email = (body.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    tenant_id = _get_tenant_id(body.tenant_id)
    user = get_auth_service().get_user_by_email(email, tenant_id)

    if user:
        return {
            "ok": True,
            "exists": True,
            "user": {
                "display_name": user.display_name,
                "first_name": user.first_name,
                "is_onboarded": user.is_onboarded,
            },
        }
    return {"ok": True, "exists": False}


@router.get("/activities")
def list_activities():
    with get_db_session() as session:
        activities = (
            session.query(Activity)
            .filter(Activity.is_active == True)
            .order_by(Activity.display_order)
            .all()
        )
        return {
            "ok": True,
            "activities": [
                {
                    "activity_code": a.activity_code,
                    "label": a.label,
                    "description": a.description,
                }
                for a in activities
            ],
        }


@router.put("/preferences")
def update_preferences(body: PreferencesBody, user: AppUser = Depends(_get_current_user)):
    with get_db_session() as session:
        db_user = (
            session.query(AppUser).filter(AppUser.user_id == user.user_id).first()
        )
        if not db_user:
            raise HTTPException(status_code=404, detail="User not found")

        if body.preferred_name is not None:
            db_user.preferred_name = body.preferred_name
        if body.timezone is not None:
            db_user.timezone = body.timezone
        if body.locale is not None:
            db_user.locale = body.locale

        preference = (
            session.query(UserPreference).filter(UserPreference.user_id == user.user_id).first()
        )
        if not preference:
            preference = UserPreference(user_id=user.user_id)
            session.add(preference)

        if body.tone is not None:
            preference.tone = body.tone
        if body.greeting_enabled is not None:
            preference.greeting_enabled = body.greeting_enabled
        if body.autonomy_routine_tasks is not None:
            preference.autonomy_routine_tasks = body.autonomy_routine_tasks
        if body.autonomy_sensitive_tasks is not None:
            preference.autonomy_sensitive_tasks = body.autonomy_sensitive_tasks

        if body.activities is not None:
            session.query(UserActivity).filter(UserActivity.user_id == user.user_id).delete()
            for i, code in enumerate(body.activities):
                activity = (
                    session.query(Activity)
                    .filter(Activity.activity_code == code)
                    .first()
                )
                if activity:
                    session.add(
                        UserActivity(
                            user_id=user.user_id,
                            activity_id=activity.activity_id,
                            is_primary=(i == 0),
                        )
                    )

        # If user hasn't completed onboarding yet, saving preferences counts —
        # set the timestamp so the is_onboarded property flips, /me returns
        # a non-null profile, and personalization applies.
        # (is_onboarded is a read-only @property derived from this timestamp.)
        if db_user.onboarding_completed_at is None:
            db_user.onboarding_completed_at = datetime.utcnow()

        session.commit()

    profile = get_auth_service().regenerate_user_profile(user.user_id)
    return {"ok": True, "message": "Preferences updated", "profile": profile}
