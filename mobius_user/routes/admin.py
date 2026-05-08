"""FastAPI router for admin endpoints.

Mount with: app.include_router(admin_router, prefix="/api/v1/admin")

Auth: bearer token + email allowlist (MOBIUS_USER_ADMIN_EMAILS).
For v1 dev, this is the gate. Replace with role-based access (is_admin
column on app_user) once the role table is wired into the auth flow.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from mobius_user.db.session import get_db_session
from mobius_user.models.activity import Activity, UserActivity
from mobius_user.models.preference import UserPreference
from mobius_user.models.tenant import AppUser, AuthProviderLink, UserSession
from mobius_user.services.auth_service import get_auth_service, get_user_from_token
from mobius_user.services.prompt_builder import CURRENT_TEMPLATE_VERSION

try:
    from fastapi import APIRouter, HTTPException, Request, Query
except ImportError:
    raise ImportError(
        "FastAPI is required for admin routes. Install with: pip install mobius-user[fastapi]"
    )

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


def _allowlist() -> set[str]:
    raw = os.getenv("MOBIUS_USER_ADMIN_EMAILS") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _require_admin(request: Request) -> AppUser:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = get_user_from_token(auth_header[7:])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    allow = _allowlist()
    if not allow:
        # Empty allowlist = nobody allowed. Don't fall open.
        raise HTTPException(
            status_code=403,
            detail="Admin endpoints disabled (MOBIUS_USER_ADMIN_EMAILS unset)",
        )
    email = (user.email or "").strip().lower()
    if email not in allow:
        raise HTTPException(status_code=403, detail="Forbidden")
    return user


@router.get("/users")
def list_users(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: Optional[str] = Query(None, description="Free-text email/name search"),
):
    _require_admin(request)
    with get_db_session() as session:
        query = session.query(AppUser).filter(AppUser.status == "active")
        if q:
            like = f"%{q.lower()}%"
            query = query.filter(
                (AppUser.email.ilike(like))
                | (AppUser.first_name.ilike(like))
                | (AppUser.preferred_name.ilike(like))
                | (AppUser.display_name.ilike(like))
            )
        total = query.count()
        rows = (
            query.order_by(AppUser.created_at.desc()).offset(offset).limit(limit).all()
        )

        # Bulk-load preference + provider data for these users.
        user_ids = [u.user_id for u in rows]
        prefs_by_user = {}
        for p in (
            session.query(UserPreference)
            .filter(UserPreference.user_id.in_(user_ids))
            .all()
            if user_ids
            else []
        ):
            prefs_by_user[p.user_id] = p

        providers_by_user: dict = {}
        for link in (
            session.query(AuthProviderLink)
            .filter(AuthProviderLink.user_id.in_(user_ids))
            .all()
            if user_ids
            else []
        ):
            providers_by_user.setdefault(link.user_id, []).append(link.provider)

        users = []
        for u in rows:
            pref = prefs_by_user.get(u.user_id)
            users.append({
                "user_id": str(u.user_id),
                "email": u.email,
                "first_name": u.first_name,
                "display_name": u.display_name,
                "preferred_name": u.preferred_name,
                "is_onboarded": u.is_onboarded,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "auth_providers": sorted(providers_by_user.get(u.user_id, [])),
                "has_profile": bool(pref and pref.profile_json),
                "profile_version": pref.profile_version if pref else None,
            })

    return {
        "ok": True,
        "users": users,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/users/{user_id}")
def get_user_detail(user_id: str, request: Request):
    _require_admin(request)
    import uuid as _uuid

    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    auth_service = get_auth_service()

    with get_db_session() as session:
        u = session.query(AppUser).filter(AppUser.user_id == uid).first()
        if not u:
            raise HTTPException(status_code=404, detail="User not found")

        pref = (
            session.query(UserPreference)
            .filter(UserPreference.user_id == uid)
            .first()
        )

        activities = []
        for ua in (
            session.query(UserActivity).filter(UserActivity.user_id == uid).all()
        ):
            a = (
                session.query(Activity)
                .filter(Activity.activity_id == ua.activity_id)
                .first()
            )
            if a:
                activities.append({
                    "activity_code": a.activity_code,
                    "label": a.label,
                    "is_primary": ua.is_primary,
                })

        providers = [
            {
                "provider": link.provider,
                "email": link.email,
                "linked_at": link.created_at.isoformat() if link.created_at else None,
            }
            for link in session.query(AuthProviderLink)
            .filter(AuthProviderLink.user_id == uid)
            .all()
        ]

        sessions_count = (
            session.query(UserSession)
            .filter(
                UserSession.user_id == uid,
                UserSession.revoked_at.is_(None),
            )
            .count()
        )

        # Lazy-regen profile if stale (same logic as /me).
        profile = pref.profile_json if pref else None
        if pref and (
            not pref.profile_json or pref.profile_version != CURRENT_TEMPLATE_VERSION
        ):
            profile = auth_service.regenerate_user_profile(uid)

    return {
        "ok": True,
        "user": {
            "user_id": str(u.user_id),
            "tenant_id": str(u.tenant_id),
            "email": u.email,
            "first_name": u.first_name,
            "display_name": u.display_name,
            "preferred_name": u.preferred_name,
            "timezone": u.timezone,
            "locale": u.locale,
            "avatar_url": u.avatar_url,
            "is_onboarded": u.is_onboarded,
            "status": u.status,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "onboarding_completed_at": (
                u.onboarding_completed_at.isoformat() if u.onboarding_completed_at else None
            ),
            "activities": activities,
            "auth_providers": providers,
            "active_sessions": sessions_count,
            "preference": (
                {
                    "tone": pref.tone,
                    "greeting_enabled": pref.greeting_enabled,
                    "ai_experience_level": pref.ai_experience_level,
                    "autonomy_routine_tasks": pref.autonomy_routine_tasks,
                    "autonomy_sensitive_tasks": pref.autonomy_sensitive_tasks,
                }
                if pref
                else None
            ),
            "profile": profile,
        },
    }
