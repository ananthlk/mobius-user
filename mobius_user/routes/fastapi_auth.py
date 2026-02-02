"""
FastAPI router for auth routes.

Mount with: app.include_router(fastapi_auth_router, prefix="/api/v1/auth")
Requires: fastapi (pip install mobius-user[fastapi])
"""

import os
import uuid
from datetime import datetime
from typing import Any, Optional

from mobius_user.services.auth_service import get_auth_service, get_user_from_token
from mobius_user.db.session import get_db_session
from mobius_user.models.tenant import AppUser
from mobius_user.models.activity import Activity, UserActivity
from mobius_user.models.preference import UserPreference

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

    return {"ok": True, "message": "Registration successful", **auth_response}


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

    return {"ok": True, "message": "Onboarding completed"}


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

        session.commit()

    return {"ok": True, "message": "Preferences updated"}
