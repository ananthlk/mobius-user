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
from mobius_user.services.prompt_builder import (
    CURRENT_TEMPLATE_VERSION,
    build_user_profile,
)


def _get_jwt_secret() -> str:
    return os.getenv("JWT_SECRET", "mobius-user-secret-key-change-in-production")


def _get_access_token_expire_minutes() -> int:
    return int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))


def _get_refresh_token_expire_days() -> int:
    return int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))


def _get_google_client_id() -> str:
    """Web-application OAuth client ID. Required for /auth/google."""
    return (os.getenv("GOOGLE_CLIENT_ID") or "").strip()


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
                # Policy (Ananth, 2026-07-17): invited accounts do NOT
                # auto-activate on Google sign-in — explicit set-password
                # activation is required. OFF is the code default;
                # MOBIUS_INVITE_GOOGLE_ACTIVATION=1 is the explicit escape
                # hatch if an org ever opts back in.
                if (
                    existing.status == "invited"
                    and os.getenv("MOBIUS_INVITE_GOOGLE_ACTIVATION", "0") == "1"
                ):
                    existing.status = "active"
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

    def _issue_session_for_user(
        self,
        user_id: uuid.UUID,
        device_info: Optional[dict] = None,
    ) -> dict:
        """Issue tokens + auth_response envelope for an already-authenticated user.

        Pulls a fresh AppUser inside this session so the response always reflects
        current DB state (display_name, preferred_name, is_onboarded) — important
        for a user we just created via OAuth.
        """
        with get_db_session() as session:
            user = (
                session.query(AppUser)
                .filter(
                    AppUser.user_id == user_id,
                    AppUser.status == "active",
                )
                .first()
            )
            if not user:
                raise RuntimeError(f"User {user_id} not found while issuing session")

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

            # Surface the user profile envelope. Lazy-regenerate if the
            # stored version is older than the current template — pure
            # in-process work, no LLM, so we do it inline.
            profile_envelope: Optional[dict] = None
            if preference is not None:
                stored = preference.profile_json
                stored_version = preference.profile_version
                if stored and stored_version == CURRENT_TEMPLATE_VERSION:
                    profile_envelope = stored
                else:
                    profile_envelope = self._build_profile_envelope(
                        user, preference, activities
                    )
                    preference.profile_json = profile_envelope
                    preference.profile_version = CURRENT_TEMPLATE_VERSION
                    preference.profile_generated_at = datetime.utcnow()
                    session.commit()

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
                    "profile": profile_envelope,
                },
            }

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

            user_id = user.user_id

        return self._issue_session_for_user(user_id, device_info=device_info), None

    # =========================================================================
    # Google Sign-In (ID-token verification, sign-in only)
    # =========================================================================

    def verify_google_id_token(
        self, id_token_str: str
    ) -> Tuple[Optional[dict], Optional[str]]:
        """Verify a Google ID token. Returns (claims, error).

        Requires GOOGLE_CLIENT_ID set in env. Uses google-auth's
        id_token.verify_oauth2_token, which validates signature against Google's
        JWKS, expiry, and audience.
        """
        client_id = _get_google_client_id()
        if not client_id:
            return None, "Google sign-in not configured (GOOGLE_CLIENT_ID missing)"
        if not id_token_str or not id_token_str.strip():
            return None, "Missing id_token"
        try:
            from google.oauth2 import id_token as google_id_token
            from google.auth.transport import requests as google_requests
        except ImportError:
            return None, "google-auth not installed on server"
        try:
            claims = google_id_token.verify_oauth2_token(
                id_token_str,
                google_requests.Request(),
                client_id,
            )
        except ValueError as exc:
            return None, f"Invalid Google ID token: {exc}"

        if claims.get("iss") not in (
            "accounts.google.com",
            "https://accounts.google.com",
        ):
            return None, "Invalid token issuer"
        if not claims.get("email"):
            return None, "Google account has no email claim"
        if claims.get("email_verified") is False:
            return None, "Google email is not verified"
        return claims, None

    def authenticate_google(
        self,
        id_token_str: str,
        tenant_id: uuid.UUID,
        device_info: Optional[dict] = None,
    ) -> Tuple[Optional[dict], bool, Optional[str]]:
        """Authenticate (or auto-create) a user from a Google ID token.

        Returns:
            (auth_response, is_new_user, error_message)
        """
        claims, err = self.verify_google_id_token(id_token_str)
        if err:
            return None, False, err

        provider_user_id = str(claims["sub"])
        email = str(claims["email"]).strip().lower()
        given_name = (claims.get("given_name") or "").strip() or None
        family_name = (claims.get("family_name") or "").strip() or None
        full_name = (claims.get("name") or "").strip() or None
        avatar_url = claims.get("picture") or None
        display_name = full_name or (
            f"{given_name} {family_name}".strip()
            if (given_name or family_name)
            else None
        )

        existing = self.get_user_by_provider("google", provider_user_id)
        is_new_user = False

        if existing is None:
            # No google link yet. create_user_from_oauth handles "email already
            # exists, link provider to existing user" — in that case we
            # shouldn't claim is_new_user.
            email_match = self.get_user_by_email(email, tenant_id)
            user = self.create_user_from_oauth(
                tenant_id=tenant_id,
                provider="google",
                provider_user_id=provider_user_id,
                email=email,
                display_name=display_name,
                first_name=given_name,
                avatar_url=avatar_url,
            )
            is_new_user = email_match is None
        else:
            user = existing

        return (
            self._issue_session_for_user(user.user_id, device_info=device_info),
            is_new_user,
            None,
        )

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

    # =========================================================================
    # User profile (preferences → structured envelope for consumer modules)
    # =========================================================================

    def _build_profile_envelope(
        self,
        user: AppUser,
        preference: Optional[UserPreference],
        activities_resolved: list[dict],
    ) -> dict:
        """Build the profile envelope from already-loaded model instances."""
        return build_user_profile(
            preferred_name=user.preferred_name,
            first_name=user.first_name,
            display_name=user.display_name,
            timezone_str=user.timezone,
            activities=[
                {"code": a["activity_code"], "label": a["label"]}
                for a in activities_resolved
            ],
            tone=preference.tone if preference else None,
            ai_experience_level=preference.ai_experience_level if preference else None,
            greeting_enabled=preference.greeting_enabled if preference else True,
            autonomy_routine_tasks=preference.autonomy_routine_tasks if preference else None,
            autonomy_sensitive_tasks=preference.autonomy_sensitive_tasks if preference else None,
        )

    def regenerate_user_profile(self, user_id: uuid.UUID) -> Optional[dict]:
        """Build and persist the user profile envelope.

        Called from write paths (PUT /onboarding, PUT /preferences) and from
        read paths when profile_version is stale or null. Idempotent.
        Returns the freshly-built envelope, or None if the user has no
        preference row (brand-new account, no onboarding yet).
        """
        with get_db_session() as session:
            user = (
                session.query(AppUser)
                .filter(AppUser.user_id == user_id)
                .first()
            )
            if not user:
                return None

            preference = (
                session.query(UserPreference)
                .filter(UserPreference.user_id == user_id)
                .first()
            )

            user_activities = (
                session.query(UserActivity)
                .filter(UserActivity.user_id == user_id)
                .all()
            )
            activities_resolved: list[dict] = []
            for ua in user_activities:
                activity = (
                    session.query(Activity)
                    .filter(Activity.activity_id == ua.activity_id)
                    .first()
                )
                if activity:
                    activities_resolved.append({
                        "activity_code": activity.activity_code,
                        "label": activity.label,
                        "is_primary": ua.is_primary,
                    })

            envelope = self._build_profile_envelope(
                user, preference, activities_resolved
            )

            if preference is None:
                # No preference row yet — return the envelope but don't persist.
                # Onboarding will create the row and persist on its own regen call.
                return envelope

            preference.profile_json = envelope
            preference.profile_version = CURRENT_TEMPLATE_VERSION
            preference.profile_generated_at = datetime.utcnow()
            session.commit()
            return envelope

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
