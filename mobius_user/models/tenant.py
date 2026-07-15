"""
Tenant, Role, User, and Auth models.

Owned by mobius_user database.
"""

import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB
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

    is_agent = Column(Boolean, default=False, nullable=False)
    # Agents keep their pre-existing assignee handle ("agent:{name}");
    # humans have no canonical_handle and derive "user:{user_id}".
    canonical_handle = Column(String(255), nullable=True, unique=True)

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
    aliases = relationship(
        "UserAlias", back_populates="user", cascade="all, delete-orphan"
    )
    org_memberships = relationship(
        "UserOrgMembership", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def assignee_ref(self) -> str:
        """Canonical task-assignee reference.

        Contract with mobius-task-manager: humans are ``user:{user_id}``,
        agents keep their grandfathered ``agent:{name}`` handle.
        """
        return self.canonical_handle or f"user:{self.user_id}"

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


class UserAlias(Base):
    """Natural-language handles for a user ("sam", "sammy").

    Powers ranked candidate resolution in /users/resolve. Collisions across
    users are expected — the resolver ranks, the caller disambiguates.
    """

    __tablename__ = "user_alias"
    __table_args__ = (UniqueConstraint("user_id", "alias", name="uq_user_alias_user_alias"),)

    alias_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    alias = Column(String(255), nullable=False)
    weight = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("AppUser", back_populates="aliases")


class UserOrgMembership(Base):
    """Org scoping for task queues and sign-in context.

    org_slug is the canonical key from the master org registry owned by the
    provider-roster-credentialing service (Ananth's ownership call,
    2026-07-08) — validated against its API at write time, no cross-DB FK.
    org_display_name is denormalized at write time so reads never call the
    master. roles uses the task-routing vocabulary (open set:
    credentialing_coordinator, rag_admin, corpus_curator, ...); membership
    lives here, which roles route which task types is owned by the
    task-manager contract layer.
    """

    __tablename__ = "user_org_membership"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    org_slug = Column(String(255), primary_key=True)
    org_display_name = Column(String(255), nullable=True)
    roles = Column(ARRAY(String(100)), default=list, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("AppUser", back_populates="org_memberships")


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


class AuthToken(Base):
    """Single-use, expiring tokens for invite / password-reset links.

    Only sha256(raw_token) is stored; the raw token lives exclusively in
    the emailed link. consumed_at enforces single use.
    """

    __tablename__ = "auth_token"

    token_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose = Column(String(20), nullable=False)  # invite | reset
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(255), nullable=True)

    @property
    def is_valid(self) -> bool:
        if self.consumed_at:
            return False
        return datetime.utcnow() < self.expires_at
