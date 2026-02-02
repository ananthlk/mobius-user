"""
Flask Blueprint for auth routes.

Mount at /api/v1/auth (or your chosen prefix).
Requires: flask (pip install mobius-user[flask])
"""

import os
import uuid
from datetime import datetime

from mobius_user.services.auth_service import get_auth_service
from mobius_user.db.session import get_db_session
from mobius_user.models.tenant import AppUser
from mobius_user.models.activity import Activity, UserActivity
from mobius_user.models.preference import UserPreference

try:
    from flask import Blueprint, request, jsonify
except ImportError:
    raise ImportError("Flask is required for flask_auth. Install with: pip install mobius-user[flask]")


bp = Blueprint("mobius_auth", __name__, url_prefix="/api/v1/auth")

DEFAULT_TENANT_ID = uuid.UUID(
    os.getenv("DEFAULT_TENANT_ID", "00000000-0000-0000-0000-000000000001")
)


def _get_tenant_id(data: dict) -> uuid.UUID:
    tenant_id = data.get("tenant_id")
    if tenant_id:
        try:
            return uuid.UUID(tenant_id)
        except ValueError:
            pass
    return DEFAULT_TENANT_ID


def _get_current_user():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    return get_auth_service().validate_access_token(token)


@bp.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip()
    first_name = (data.get("first_name") or "").strip()
    tenant_id = _get_tenant_id(data)

    if not email:
        return jsonify({"error": "Email is required"}), 400
    if not password:
        return jsonify({"error": "Password is required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    auth_service = get_auth_service()
    auth_service.get_or_create_default_tenant()

    user, error = auth_service.register_user(
        tenant_id=tenant_id,
        email=email,
        password=password,
        display_name=display_name or None,
        first_name=first_name or None,
    )

    if error:
        return jsonify({"error": error}), 400

    auth_response, _ = auth_service.authenticate_email(
        email=email, password=password, tenant_id=tenant_id
    )

    return jsonify({"ok": True, "message": "Registration successful", **auth_response})


@bp.route("/login", methods=["POST"])
def login():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    tenant_id = _get_tenant_id(data)
    device_info = data.get("device_info")

    if not email:
        return jsonify({"error": "Email is required"}), 400
    if not password:
        return jsonify({"error": "Password is required"}), 400

    auth_response, error = get_auth_service().authenticate_email(
        email=email,
        password=password,
        tenant_id=tenant_id,
        device_info=device_info,
    )

    if error:
        return jsonify({"error": error}), 401

    return jsonify({"ok": True, **auth_response})


@bp.route("/refresh", methods=["POST"])
def refresh():
    data = request.json or {}
    refresh_token = data.get("refresh_token") or ""

    if not refresh_token:
        return jsonify({"error": "Refresh token is required"}), 400

    auth_response, error = get_auth_service().refresh_access_token(refresh_token)

    if error:
        return jsonify({"error": error}), 401

    return jsonify({"ok": True, **auth_response})


@bp.route("/logout", methods=["POST"])
def logout():
    data = request.json or {}
    refresh_token = data.get("refresh_token") or ""

    if refresh_token:
        get_auth_service().logout(refresh_token)

    return jsonify({"ok": True, "message": "Logged out"})


@bp.route("/me", methods=["GET"])
def me():
    user = _get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

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
                activities.append(
                    {
                        "activity_code": activity.activity_code,
                        "label": activity.label,
                        "is_primary": ua.is_primary,
                    }
                )
        preference = (
            session.query(UserPreference)
            .filter(UserPreference.user_id == user.user_id)
            .first()
        )

    return jsonify({
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
    })


@bp.route("/onboarding", methods=["PUT"])
def complete_onboarding():
    user = _get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    preferred_name = (data.get("preferred_name") or "").strip()
    activity_codes = data.get("activities") or []
    ai_experience_level = data.get("ai_experience_level") or "beginner"
    autonomy_routine_tasks = data.get("autonomy_routine_tasks") or "confirm_first"
    autonomy_sensitive_tasks = data.get("autonomy_sensitive_tasks") or "confirm_first"
    tone = data.get("tone") or "professional"
    greeting_enabled = data.get("greeting_enabled", True)
    timezone = data.get("timezone") or "America/New_York"

    with get_db_session() as session:
        db_user = (
            session.query(AppUser).filter(AppUser.user_id == user.user_id).first()
        )
        if not db_user:
            return jsonify({"error": "User not found"}), 404

        if preferred_name:
            db_user.preferred_name = preferred_name
        db_user.timezone = timezone
        db_user.onboarding_completed_at = datetime.utcnow()

        preference = (
            session.query(UserPreference).filter(UserPreference.user_id == user.user_id).first()
        )
        if not preference:
            preference = UserPreference(user_id=user.user_id)
            session.add(preference)

        preference.tone = tone
        preference.greeting_enabled = greeting_enabled
        preference.ai_experience_level = ai_experience_level
        preference.autonomy_routine_tasks = autonomy_routine_tasks
        preference.autonomy_sensitive_tasks = autonomy_sensitive_tasks

        session.query(UserActivity).filter(UserActivity.user_id == user.user_id).delete()

        for i, code in enumerate(activity_codes):
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

    return jsonify({"ok": True, "message": "Onboarding completed"})


@bp.route("/check-email", methods=["POST"])
def check_email():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    tenant_id = _get_tenant_id(data)

    if not email:
        return jsonify({"error": "Email is required"}), 400

    user = get_auth_service().get_user_by_email(email, tenant_id)

    if user:
        return jsonify({
            "ok": True,
            "exists": True,
            "user": {
                "display_name": user.display_name,
                "first_name": user.first_name,
                "is_onboarded": user.is_onboarded,
            },
        })
    return jsonify({"ok": True, "exists": False})


@bp.route("/activities", methods=["GET"])
def list_activities():
    with get_db_session() as session:
        activities = (
            session.query(Activity)
            .filter(Activity.is_active == True)
            .order_by(Activity.display_order)
            .all()
        )
        return jsonify({
            "ok": True,
            "activities": [
                {
                    "activity_code": a.activity_code,
                    "label": a.label,
                    "description": a.description,
                }
                for a in activities
            ],
        })


@bp.route("/preferences", methods=["PUT"])
def update_preferences():
    user = _get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}

    with get_db_session() as session:
        db_user = (
            session.query(AppUser).filter(AppUser.user_id == user.user_id).first()
        )
        if not db_user:
            return jsonify({"error": "User not found"}), 404

        if "preferred_name" in data:
            db_user.preferred_name = data["preferred_name"]
        if "timezone" in data:
            db_user.timezone = data["timezone"]
        if "locale" in data:
            db_user.locale = data["locale"]

        preference = (
            session.query(UserPreference).filter(UserPreference.user_id == user.user_id).first()
        )
        if not preference:
            preference = UserPreference(user_id=user.user_id)
            session.add(preference)

        if "tone" in data:
            preference.tone = data["tone"]
        if "greeting_enabled" in data:
            preference.greeting_enabled = data["greeting_enabled"]
        if "autonomy_routine_tasks" in data:
            preference.autonomy_routine_tasks = data["autonomy_routine_tasks"]
        if "autonomy_sensitive_tasks" in data:
            preference.autonomy_sensitive_tasks = data["autonomy_sensitive_tasks"]

        if "activities" in data:
            session.query(UserActivity).filter(UserActivity.user_id == user.user_id).delete()
            for i, code in enumerate(data["activities"]):
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

    return jsonify({"ok": True, "message": "Preferences updated"})
