"""
Tenant, Role, User, and Auth models.

Owned by mobius_user database.
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from mobius_user.db.session import Base


class Tenant(Base):
    """Tenant table."""

    __tablename__ = "tenant"

    tenant_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    users = relationship("AppUser", back_populates="tenant")


class Role(Base):
    """Role table."""

    __tablename__ = "role"

    role_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    users = relationship("AppUser", back_populates="role")


class AppUser(Base):
    """Application user table."""

    __tablename__ = "app_user"

    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenant.tenant_id"), nullable=False)
    role_id = Column(UUID(as_uuid=True), ForeignKey("role.role_id"), nullable=True)
    email = Column(String(255), nullable=True)
    display_name = Column(String(255), nullable=True)
    status = Column(String(50), default="active", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    password_hash = Column(String(255), nullable=True)
    first_name = Column(String(100), nullable=True)
    preferred_name = Column(String(100), nullable=True)
    timezone = Column(String(50), default="America/New_York", nullable=True)
    locale = Column(String(10), default="en-US", nullable=True)
    onboarding_completed_at = Column(DateTime, nullable=True)
    avatar_url = Column(String(500), nullable=True)

    tenant = relationship("Tenant", back_populates="users")
    role = relationship("Role", back_populates="users")
    auth_providers = relationship(
        "AuthProviderLink", back_populates="user", cascade="all, delete-orphan"
    )
    sessions = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )
    activities = relationship(
        "UserActivity", back_populates="user", cascade="all, delete-orphan"
    )
    preference = relationship(
        "UserPreference", back_populates="user", uselist=False
    )

    @property
    def greeting_name(self) -> str:
        return self.preferred_name or self.first_name or self.display_name or "there"

    @property
    def is_onboarded(self) -> bool:
        return self.onboarding_completed_at is not None


class AuthProviderLink(Base):
    """Links external auth providers to Mobius accounts."""

    __tablename__ = "auth_provider_link"

    link_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    provider = Column(String(50), nullable=False)
    provider_user_id = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)

    user = relationship("AppUser", back_populates="auth_providers")


class UserSession(Base):
    """Active user sessions for token management."""

    __tablename__ = "user_session"

    session_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    refresh_token_hash = Column(String(255), nullable=True)
    device_info = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("AppUser", back_populates="sessions")

    @property
    def is_valid(self) -> bool:
        if self.revoked_at:
            return False
        return datetime.utcnow() < self.expires_at
