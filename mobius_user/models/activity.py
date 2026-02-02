"""
Activity models - what users do (verify eligibility, submit claims, etc.).
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Boolean, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from mobius_user.db.session import Base


class Activity(Base):
    """Reference data for user activities."""

    __tablename__ = "activity"

    activity_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_code = Column(String(50), unique=True, nullable=False)
    label = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    quick_actions = Column(JSONB, nullable=True)
    relevant_data_fields = Column(JSONB, nullable=True)
    display_order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user_activities = relationship("UserActivity", back_populates="activity")

    def to_dict(self) -> dict:
        return {
            "activity_id": str(self.activity_id),
            "activity_code": self.activity_code,
            "label": self.label,
            "description": self.description,
            "quick_actions": self.quick_actions or [],
            "relevant_data_fields": self.relevant_data_fields or [],
        }


class UserActivity(Base):
    """Many-to-many link between users and activities."""

    __tablename__ = "user_activity"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    activity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("activity.activity_id", ondelete="CASCADE"),
        primary_key=True,
    )
    is_primary = Column(Boolean, default=False, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("AppUser", back_populates="activities")
    activity = relationship("Activity", back_populates="user_activities")


ACTIVITY_CODES = [
    "schedule_appointments",
    "check_in_patients",
    "verify_eligibility",
    "submit_claims",
    "rework_denials",
    "prior_authorization",
    "patient_outreach",
    "document_notes",
    "coordinate_referrals",
]
