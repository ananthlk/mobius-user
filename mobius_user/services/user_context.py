"""
User Context Service - loads and hydrates full user profile.

Used by Mini and other surfaces for personalization.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from mobius_user.db.session import get_db_session
from mobius_user.models.tenant import AppUser
from mobius_user.models.activity import Activity, UserActivity
from mobius_user.models.preference import UserPreference


@dataclass
class UserProfile:
    """Complete user profile for personalization."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: Optional[str] = None

    display_name: Optional[str] = None
    first_name: Optional[str] = None
    preferred_name: Optional[str] = None
    avatar_url: Optional[str] = None

    timezone: str = "America/New_York"
    locale: str = "en-US"

    is_onboarded: bool = False
    onboarding_completed_at: Optional[datetime] = None

    activities: List[Dict[str, Any]] = field(default_factory=list)
    activity_codes: List[str] = field(default_factory=list)
    primary_activity: Optional[str] = None

    tone: str = "professional"
    greeting_enabled: bool = True
    ai_experience_level: str = "beginner"
    autonomy_routine_tasks: str = "confirm_first"
    autonomy_sensitive_tasks: str = "confirm_first"

    quick_actions: List[Dict[str, str]] = field(default_factory=list)
    relevant_data_fields: List[str] = field(default_factory=list)

    @property
    def greeting_name(self) -> str:
        return self.preferred_name or self.first_name or self.display_name or "there"

    def get_default_execution_mode(self, is_sensitive: bool = False) -> str:
        autonomy = (
            self.autonomy_sensitive_tasks if is_sensitive else self.autonomy_routine_tasks
        )
        mode_map = {
            "automatic": "agentic",
            "confirm_first": "copilot",
            "manual": "user_driven",
        }
        return mode_map.get(autonomy, "copilot")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": str(self.user_id),
            "tenant_id": str(self.tenant_id),
            "email": self.email,
            "display_name": self.display_name,
            "first_name": self.first_name,
            "preferred_name": self.preferred_name,
            "greeting_name": self.greeting_name,
            "avatar_url": self.avatar_url,
            "timezone": self.timezone,
            "locale": self.locale,
            "is_onboarded": self.is_onboarded,
            "activities": self.activities,
            "activity_codes": self.activity_codes,
            "primary_activity": self.primary_activity,
            "tone": self.tone,
            "greeting_enabled": self.greeting_enabled,
            "ai_experience_level": self.ai_experience_level,
            "autonomy_routine_tasks": self.autonomy_routine_tasks,
            "autonomy_sensitive_tasks": self.autonomy_sensitive_tasks,
            "quick_actions": self.quick_actions,
            "relevant_data_fields": self.relevant_data_fields,
        }


QUICK_ACTION_LABELS: Dict[str, str] = {
    "find_available_slot": "Find available slot",
    "reschedule": "Reschedule",
    "cancel_appointment": "Cancel appointment",
    "verify_demographics": "Verify demographics",
    "collect_copay": "Collect copay",
    "update_insurance": "Update insurance",
    "run_eligibility_check": "Check eligibility",
    "update_coverage": "Update coverage",
    "flag_issue": "Flag issue",
    "submit_claim": "Submit claim",
    "check_status": "Check status",
    "view_remittance": "View remittance",
    "view_denial_reason": "View denial reason",
    "appeal_claim": "Appeal claim",
    "correct_and_resubmit": "Correct & resubmit",
    "submit_auth_request": "Submit auth request",
    "check_auth_status": "Check auth status",
    "upload_clinical": "Upload clinical docs",
    "send_reminder": "Send reminder",
    "log_call": "Log call",
    "schedule_callback": "Schedule callback",
    "add_note": "Add note",
    "view_history": "View history",
    "flag_for_review": "Flag for review",
    "create_referral": "Create referral",
    "check_referral_status": "Check referral status",
    "upload_documents": "Upload documents",
}


class UserContextService:
    """Service for loading and managing user context."""

    def __init__(self):
        self._cache: Dict[uuid.UUID, UserProfile] = {}

    def get_user_profile(self, user_id: uuid.UUID) -> Optional[UserProfile]:
        if user_id in self._cache:
            return self._cache[user_id]

        with get_db_session() as session:
            user = (
                session.query(AppUser)
                .filter(AppUser.user_id == user_id, AppUser.status == "active")
                .first()
            )

            if not user:
                return None

            user_activities = (
                session.query(UserActivity).filter(UserActivity.user_id == user_id).all()
            )

            activities = []
            activity_codes = []
            primary_activity = None
            all_quick_actions = []
            all_data_fields = []

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
                            "quick_actions": activity.quick_actions or [],
                            "relevant_data_fields": activity.relevant_data_fields or [],
                        }
                    )
                    activity_codes.append(activity.activity_code)

                    if ua.is_primary:
                        primary_activity = activity.activity_code

                    if activity.quick_actions:
                        for action in activity.quick_actions:
                            if action not in [a["code"] for a in all_quick_actions]:
                                all_quick_actions.append(
                                    {
                                        "code": action,
                                        "label": self._action_to_label(action),
                                        "from_activity": activity.activity_code,
                                    }
                                )

                    if activity.relevant_data_fields:
                        for field in activity.relevant_data_fields:
                            if field not in all_data_fields:
                                all_data_fields.append(field)

            preference = (
                session.query(UserPreference).filter(UserPreference.user_id == user_id).first()
            )

            profile = UserProfile(
                user_id=user.user_id,
                tenant_id=user.tenant_id,
                email=user.email,
                display_name=user.display_name,
                first_name=user.first_name,
                preferred_name=user.preferred_name,
                avatar_url=user.avatar_url,
                timezone=user.timezone or "America/New_York",
                locale=user.locale or "en-US",
                is_onboarded=user.is_onboarded,
                onboarding_completed_at=user.onboarding_completed_at,
                activities=activities,
                activity_codes=activity_codes,
                primary_activity=primary_activity,
                tone=preference.tone if preference else "professional",
                greeting_enabled=preference.greeting_enabled if preference else True,
                ai_experience_level=(
                    preference.ai_experience_level if preference else "beginner"
                ),
                autonomy_routine_tasks=(
                    preference.autonomy_routine_tasks if preference else "confirm_first"
                ),
                autonomy_sensitive_tasks=(
                    preference.autonomy_sensitive_tasks if preference else "confirm_first"
                ),
                quick_actions=all_quick_actions,
                relevant_data_fields=all_data_fields,
            )

            self._cache[user_id] = profile
            return profile

    def get_user_profile_by_email(
        self, email: str, tenant_id: uuid.UUID
    ) -> Optional[UserProfile]:
        with get_db_session() as session:
            user = (
                session.query(AppUser)
                .filter(
                    AppUser.email == email,
                    AppUser.tenant_id == tenant_id,
                    AppUser.status == "active",
                )
                .first()
            )

            if not user:
                return None

            return self.get_user_profile(user.user_id)

    def invalidate_cache(self, user_id: uuid.UUID) -> None:
        if user_id in self._cache:
            del self._cache[user_id]

    def clear_cache(self) -> None:
        self._cache.clear()

    def _action_to_label(self, action_code: str) -> str:
        return QUICK_ACTION_LABELS.get(
            action_code, action_code.replace("_", " ").title()
        )


_user_context_service: Optional[UserContextService] = None


def get_user_context_service() -> UserContextService:
    global _user_context_service
    if _user_context_service is None:
        _user_context_service = UserContextService()
    return _user_context_service
