"""
User preference model.
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, SmallInteger
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB
from sqlalchemy.orm import relationship

from mobius_user.db.session import Base


class UserPreference(Base):
    """User-level preferences."""

    __tablename__ = "user_preference"

    preference_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.user_id"),
        nullable=False,
        unique=True,
    )

    always_require_oversight = Column(Boolean, default=False, nullable=False)
    notification_preferences = Column(JSONB, nullable=True)

    tone = Column(String(20), default="professional", nullable=True)
    greeting_enabled = Column(Boolean, default=True, nullable=False)
    ai_experience_level = Column(String(20), default="beginner", nullable=True)
    autonomy_routine_tasks = Column(String(20), default="confirm_first", nullable=True)
    autonomy_sensitive_tasks = Column(String(20), default="confirm_first", nullable=True)
    display_preferences_json = Column(JSONB, nullable=True)
    # Training-mode onboarding step 5: optional hesitation chips (direct
    # fear capture). Empty = step skipped, never "no hesitations".
    hesitations = Column(ARRAY(String(50)), default=list, nullable=False)

    # Generated user profile envelope — see services/prompt_builder.py.
    # Lazy-regenerated on /me when profile_version != current template version.
    profile_json = Column(JSONB, nullable=True)
    profile_version = Column(SmallInteger, nullable=True)
    profile_generated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("AppUser", back_populates="preference")

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
