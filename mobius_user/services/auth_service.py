"""
Authentication Service for Mobius applications.

Handles:
- Email/password authentication
- JWT token generation and validation
- Session management
- OAuth token exchange (Google, Microsoft)
- User lookup and creation
"""

import os
import uuid
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple
import jwt
import bcrypt

from mobius_user.db.session import get_db_session
from mobius_user.models.tenant import AppUser, AuthProviderLink, UserSession, Tenant
from mobius_user.models.activity import Activity, UserActivity
from mobius_user.models.preference import UserPreference


def _get_jwt_secret() -> str:
    return os.getenv("JWT_SECRET", "mobius-user-secret-key-change-in-production")


def _get_access_token_expire_minutes() -> int:
    return int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))


def _get_refresh_token_expire_days() -> int:
    return int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))


class AuthService:
    """Service for handling user authentication."""

    def __init__(
        self,
        jwt_secret: Optional[str] = None,
        access_token_expire_minutes: Optional[int] = None,
        refresh_token_expire_days: Optional[int] = None,
    ):
        self.jwt_secret = jwt_secret or _get_jwt_secret()
        self.jwt_algorithm = "HS256"
        self.access_token_expire = access_token_expire_minutes or _get_access_token_expire_minutes()
        self.refresh_token_expire = refresh_token_expire_days or _get_refresh_token_expire_days()

    def hash_password(self, password: str) -> str:
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    def verify_password(self, password: str, password_hash: str) -> bool:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))

    def create_access_token(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
        expire = datetime.utcnow() + timedelta(minutes=self.access_token_expire)
        payload = {
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "exp": expire,
            "type": "access",
        }
        return jwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)

    def create_refresh_token(self, user_id: uuid.UUID, session_id: uuid.UUID) -> str:
        expire = datetime.utcnow() + timedelta(days=self.refresh_token_expire)
        payload = {
            "sub": str(user_id),
            "session_id": str(session_id),
            "exp": expire,
            "type": "refresh",
        }
        return jwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)

    def decode_token(self, token: str) -> Optional[dict]:
        try:
            return jwt.decode(token, self.jwt_secret, algorithms=[self.jwt_algorithm])
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def hash_refresh_token(self, refresh_token: str) -> str:
        return hashlib.sha256(refresh_token.encode()).hexdigest()

    def get_user_by_email(self, email: str, tenant_id: uuid.UUID) -> Optional[AppUser]:
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
            if user:
                session.expunge(user)
            return user

    def get_user_by_id(self, user_id: uuid.UUID) -> Optional[AppUser]:
        with get_db_session() as session:
            user = (
                session.query(AppUser)
                .filter(AppUser.user_id == user_id, AppUser.status == "active")
                .first()
            )
            if user:
                session.expunge(user)
            return user

    def get_user_by_provider(
        self, provider: str, provider_user_id: str
    ) -> Optional[AppUser]:
        with get_db_session() as session:
            link = (
                session.query(AuthProviderLink)
                .filter(
                    AuthProviderLink.provider == provider,
                    AuthProviderLink.provider_user_id == provider_user_id,
                )
                .first()
            )
            if link:
                user = (
                    session.query(AppUser)
                    .filter(
                        AppUser.user_id == link.user_id,
                        AppUser.status == "active",
                    )
                    .first()
                )
                if user:
                    session.expunge(user)
                return user
            return None

    def register_user(
        self,
        tenant_id: uuid.UUID,
        email: str,
        password: str,
        display_name: Optional[str] = None,
        first_name: Optional[str] = None,
    ) -> Tuple[Optional[AppUser], Optional[str]]:
        with get_db_session() as session:
            existing = (
                session.query(AppUser)
                .filter(
                    AppUser.email == email,
                    AppUser.tenant_id == tenant_id,
                )
                .first()
            )
            if existing:
                return None, "Email already registered"

            user = AppUser(
                tenant_id=tenant_id,
                email=email,
                password_hash=self.hash_password(password),
                display_name=display_name or email.split("@")[0],
                first_name=first_name,
                status="active",
            )
            session.add(user)
            session.flush()

            link = AuthProviderLink(user_id=user.user_id, provider="email", email=email)
            session.add(link)

            session.commit()
            session.refresh(user)
            session.expunge(user)

            return user, None

    def create_user_from_oauth(
        self,
        tenant_id: uuid.UUID,
        provider: str,
        provider_user_id: str,
        email: str,
        display_name: Optional[str] = None,
        first_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> AppUser:
        with get_db_session() as session:
            existing = (
                session.query(AppUser)
                .filter(AppUser.email == email, AppUser.tenant_id == tenant_id)
                .first()
            )

            if existing:
                link = AuthProviderLink(
                    user_id=existing.user_id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                    email=email,
                )
                session.add(link)
                session.commit()
                session.expunge(existing)
                return existing

            user = AppUser(
                tenant_id=tenant_id,
                email=email,
                display_name=display_name or email.split("@")[0],
                first_name=first_name,
                avatar_url=avatar_url,
                status="active",
            )
            session.add(user)
            session.flush()

            link = AuthProviderLink(
                user_id=user.user_id,
                provider=provider,
                provider_user_id=provider_user_id,
                email=email,
            )
            session.add(link)

            session.commit()
            session.refresh(user)
            session.expunge(user)

            return user

    def authenticate_email(
        self,
        email: str,
        password: str,
        tenant_id: uuid.UUID,
        device_info: Optional[dict] = None,
    ) -> Tuple[Optional[dict], Optional[str]]:
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
                return None, "Invalid email or password"

            if not user.password_hash:
                return None, "Account uses OAuth login"

            if not self.verify_password(password, user.password_hash):
                return None, "Invalid email or password"

            user_session = UserSession(
                user_id=user.user_id,
                device_info=device_info,
                expires_at=datetime.utcnow()
                + timedelta(days=self.refresh_token_expire),
            )
            session.add(user_session)
            session.flush()

            access_token = self.create_access_token(user.user_id, user.tenant_id)
            refresh_token = self.create_refresh_token(
                user.user_id, user_session.session_id
            )

            user_session.refresh_token_hash = self.hash_refresh_token(refresh_token)
            user.last_login_at = datetime.utcnow()

            session.commit()

            activities = []
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

            return {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "expires_in": self.access_token_expire * 60,
                "user": {
                    "user_id": str(user.user_id),
                    "tenant_id": str(user.tenant_id),
                    "email": user.email,
                    "display_name": user.display_name,
                    "first_name": user.first_name,
                    "preferred_name": user.preferred_name,
                    "is_onboarded": user.is_onboarded,
                    "activities": activities,
                    "preference": {
                        "tone": preference.tone if preference else "professional",
                        "greeting_enabled": (
                            preference.greeting_enabled if preference else True
                        ),
                        "autonomy_routine_tasks": (
                            preference.autonomy_routine_tasks
                            if preference
                            else "confirm_first"
                        ),
                        "autonomy_sensitive_tasks": (
                            preference.autonomy_sensitive_tasks
                            if preference
                            else "confirm_first"
                        ),
                    }
                    if preference
                    else None,
                },
            }, None

    def refresh_access_token(
        self, refresh_token: str
    ) -> Tuple[Optional[dict], Optional[str]]:
        payload = self.decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            return None, "Invalid refresh token"

        session_id = payload.get("session_id")
        user_id = payload.get("sub")

        if not session_id or not user_id:
            return None, "Invalid refresh token"

        with get_db_session() as session:
            user_session = (
                session.query(UserSession)
                .filter(
                    UserSession.session_id == uuid.UUID(session_id),
                    UserSession.user_id == uuid.UUID(user_id),
                    UserSession.revoked_at.is_(None),
                )
                .first()
            )

            if not user_session or not user_session.is_valid:
                return None, "Session expired or revoked"

            if user_session.refresh_token_hash != self.hash_refresh_token(refresh_token):
                return None, "Invalid refresh token"

            user = (
                session.query(AppUser)
                .filter(
                    AppUser.user_id == uuid.UUID(user_id),
                    AppUser.status == "active",
                )
                .first()
            )

            if not user:
                return None, "User not found"

            access_token = self.create_access_token(user.user_id, user.tenant_id)

            return {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": self.access_token_expire * 60,
            }, None

    def logout(self, refresh_token: str) -> bool:
        payload = self.decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            return False

        session_id = payload.get("session_id")
        if not session_id:
            return False

        with get_db_session() as session:
            user_session = (
                session.query(UserSession)
                .filter(UserSession.session_id == uuid.UUID(session_id))
                .first()
            )

            if user_session:
                user_session.revoked_at = datetime.utcnow()
                session.commit()
                return True

            return False

    def validate_access_token(self, access_token: str) -> Optional[AppUser]:
        payload = self.decode_token(access_token)
        if not payload or payload.get("type") != "access":
            return None

        user_id = payload.get("sub")
        if not user_id:
            return None

        return self.get_user_by_id(uuid.UUID(user_id))

    def get_or_create_default_tenant(self) -> Tenant:
        default_id = os.getenv(
            "DEFAULT_TENANT_ID", "00000000-0000-0000-0000-000000000001"
        )
        default_name = os.getenv("DEFAULT_TENANT_NAME", "Default Tenant")

        with get_db_session() as session:
            tenant = (
                session.query(Tenant)
                .filter(Tenant.tenant_id == uuid.UUID(default_id))
                .first()
            )

            if not tenant:
                tenant = Tenant(
                    tenant_id=uuid.UUID(default_id),
                    name=default_name,
                )
                session.add(tenant)
                session.commit()
                session.refresh(tenant)

            session.expunge(tenant)
            return tenant


_auth_service: Optional[AuthService] = None


def get_auth_service(
    jwt_secret: Optional[str] = None,
    access_token_expire_minutes: Optional[int] = None,
    refresh_token_expire_days: Optional[int] = None,
) -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService(
            jwt_secret=jwt_secret,
            access_token_expire_minutes=access_token_expire_minutes,
            refresh_token_expire_days=refresh_token_expire_days,
        )
    return _auth_service


def get_user_from_token(token: str) -> Optional[AppUser]:
    """Get user from access token."""
    return get_auth_service().validate_access_token(token)
