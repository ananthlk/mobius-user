"""Account lifecycle: admin-driven invites, set-password, password reset.

Built for the org-agent employee-onboarding contract (2026-07-15):
admin creates a pending account → employee gets an emailed single-use
link → sets a password → account activates. Password material never
leaves this module except as a bcrypt hash on app_user.

Token security model:
- raw token = secrets.token_urlsafe(32); only ever inside the emailed link
- stored form = sha256(raw), single-use (consumed_at), expiring
- invite TTL default 7 days (MOBIUS_INVITE_TTL_DAYS)
- reset TTL default 60 minutes (MOBIUS_RESET_TTL_MINUTES)
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

from mobius_user.db.session import get_db_session
from mobius_user.models.tenant import (
    AppUser,
    AuthProviderLink,
    AuthToken,
    UserOrgMembership,
    UserSession,
)
from mobius_user.services import lifecycle_emails

logger = logging.getLogger(__name__)

MIN_PASSWORD_LEN = 8  # matches /register


def _invite_ttl() -> timedelta:
    return timedelta(days=int(os.getenv("MOBIUS_INVITE_TTL_DAYS", "7")))


def _reset_ttl() -> timedelta:
    return timedelta(minutes=int(os.getenv("MOBIUS_RESET_TTL_MINUTES", "60")))


# ── Token primitives ──────────────────────────────────────────────────────


def generate_raw_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mask_email(email: str) -> str:
    """a•••@example.com — enough to recognise, not enough to harvest."""
    local, _, domain = (email or "").partition("@")
    if not local or not domain:
        return "•••"
    return f"{local[0]}•••@{domain}"


# ── In-process throttle (defence in depth; real limiting is at the edge) ──


class _Throttle:
    """Sliding-window counter, keyed by caller-chosen string."""

    def __init__(self, max_events: int, window_seconds: int):
        self.max = max_events
        self.window = window_seconds
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            events = [t for t in self._events.get(key, []) if now - t < self.window]
            if len(events) >= self.max:
                self._events[key] = events
                return False
            events.append(now)
            self._events[key] = events
            return True


# 5 requests / 15 min per email or IP for the public request endpoints.
reset_request_throttle = _Throttle(max_events=5, window_seconds=900)
token_info_throttle = _Throttle(max_events=30, window_seconds=300)


# ── Internal helpers ──────────────────────────────────────────────────────


def _issue_token(
    session,
    *,
    user_id: uuid.UUID,
    purpose: str,
    ttl: timedelta,
    created_by: Optional[str],
) -> Tuple[str, AuthToken]:
    """Void any open tokens of the same purpose, then mint a fresh one."""
    now = datetime.utcnow()
    open_tokens = (
        session.query(AuthToken)
        .filter(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
        )
        .all()
    )
    for t in open_tokens:
        t.consumed_at = now  # rotation: old links stop working immediately

    raw = generate_raw_token()
    token = AuthToken(
        user_id=user_id,
        purpose=purpose,
        token_hash=hash_token(raw),
        expires_at=now + ttl,
        created_by=created_by,
    )
    session.add(token)
    session.flush()
    return raw, token


def _find_valid_token(session, raw: str, purpose: str) -> Tuple[Optional[AuthToken], Optional[str]]:
    if not raw:
        return None, "token_invalid"
    token = (
        session.query(AuthToken)
        .filter(AuthToken.token_hash == hash_token(raw))
        .first()
    )
    if not token or token.purpose != purpose:
        return None, "token_invalid"
    if token.consumed_at:
        return None, "token_consumed"
    if datetime.utcnow() >= token.expires_at:
        return None, "token_expired"
    return token, None


def _upsert_membership(session, user_id: uuid.UUID, org_slug: str, roles: list[str]) -> dict:
    """Same semantics as PUT /api/v1/users/{user_id}/orgs/{org_slug}:
    slug validated against the master org registry (roster service),
    display name denormalized at write time. Definitive-unknown slug →
    membership skipped (the invite itself still proceeds — the org agent
    can PUT the membership later); master unreachable → written unvalidated.
    """
    # Lazy import: routes.users imports this module at top level.
    from mobius_user.routes.users import (
        _master_org_lookup,
        _master_org_resolve,
        _slugify,
    )

    raw = (org_slug or "").strip()
    slug = _slugify(raw)
    if not slug or raw.startswith("_"):
        return {"applied": False, "reason": "invalid_org_slug"}

    master_org, reachable = _master_org_lookup(slug)
    validated = master_org is not None
    if reachable and master_org is None:
        resolved = _master_org_resolve(raw)
        if resolved is None:
            logger.warning("invite: unknown org_slug %r — membership skipped", slug)
            return {"applied": False, "reason": "unknown_org_slug", "org_slug": slug}
        slug = resolved["org_slug"]
        master_org = {"org_name": resolved.get("display_name")}
        validated = True
    display_name = (master_org or {}).get("org_name") or slug

    clean = sorted({r.strip() for r in (roles or []) if r.strip()})
    row = (
        session.query(UserOrgMembership)
        .filter(
            UserOrgMembership.user_id == user_id,
            UserOrgMembership.org_slug == slug,
        )
        .first()
    )
    if row:
        row.roles = clean
        row.org_display_name = display_name
        # Admin-initiated add (invite path) mirrors the admin PUT: a
        # pending or removed row activates, WITH an append-only audit row
        # (no-history-loss rule).
        if row.status != "active":
            from mobius_user.routes.users import _audit_membership
            _audit_membership(session, user_id, slug, row.status, "active", "invite-add")
            row.status = "active"
            row.approved_by = "invite-add"
            row.approved_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
    else:
        session.add(
            UserOrgMembership(
                user_id=user_id,
                org_slug=slug,
                org_display_name=display_name,
                roles=clean,
                status="active",
                approved_by="invite-add",
                approved_at=datetime.utcnow(),
            )
        )
    return {"applied": True, "org_slug": slug, "org_display_name": display_name,
            "validated": validated, "roles": clean}


# ── Public API ────────────────────────────────────────────────────────────


def create_or_reinvite_user(
    *,
    tenant_id: uuid.UUID,
    email: str,
    first_name: Optional[str] = None,
    display_name: Optional[str] = None,
    org_slug: Optional[str] = None,
    roles: Optional[list[str]] = None,
    invited_by: Optional[str] = None,
) -> dict:
    """Admin-create a pending account (or rotate the invite on an existing one).

    Returns {ok, created, user_id, email, status, invite_expires_at, email_sent}
    or {ok: False, error: "email_exists", user_id, status} for active/disabled users.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return {"ok": False, "error": "invalid_email"}

    with get_db_session() as session:
        existing = (
            session.query(AppUser)
            .filter(AppUser.email == email, AppUser.tenant_id == tenant_id)
            .first()
        )

        if existing and existing.status != "invited":
            # Add-existing-user-to-org (Ananth-surfaced gap 2026-07-20):
            # an admin inviting an email that already has an account means
            # "add this person to the org" — upsert the membership instead
            # of no-op'ing. A disabled account reactivates as part of the
            # add (admin intent; capabilities stay revoked per the
            # deactivate contract). No org_slug → nothing to add → the
            # original email_exists still applies.
            if not org_slug:
                return {
                    "ok": False,
                    "error": "email_exists",
                    "user_id": str(existing.user_id),
                    "status": existing.status,
                }
            reactivated = False
            if existing.status == "disabled":
                from mobius_user.models.preference import UserPreferenceAudit
                session.add(UserPreferenceAudit(
                    user_id=existing.user_id, field="account_status",
                    old_value="disabled", new_value="active",
                    source="admin:invite-add"))
                existing.status = "active"
                reactivated = True
            membership = _upsert_membership(
                session, existing.user_id, org_slug, roles or []
            )
            session.commit()
            return {
                "ok": True,
                "created": False,
                "existing_account": True,
                "reactivated": reactivated,
                "user_id": str(existing.user_id),
                "email": email,
                "display_name": existing.display_name or existing.first_name or email,
                "status": "active",
                "email_sent": False,
                "membership": membership,
            }

        created = existing is None
        if created:
            user = AppUser(
                tenant_id=tenant_id,
                email=email,
                display_name=(display_name or "").strip() or email.split("@")[0],
                first_name=(first_name or "").strip() or None,
                status="invited",
            )
            session.add(user)
            session.flush()
        else:
            user = existing
            # Re-invite may refresh names the admin corrected.
            if display_name and display_name.strip():
                user.display_name = display_name.strip()
            if first_name and first_name.strip():
                user.first_name = first_name.strip()

        raw, token = _issue_token(
            session,
            user_id=user.user_id,
            purpose="invite",
            ttl=_invite_ttl(),
            created_by=invited_by,
        )

        membership = None
        if org_slug:
            membership = _upsert_membership(session, user.user_id, org_slug, roles or [])

        session.commit()

        user_id = str(user.user_id)
        token_id = str(token.token_id)
        expires_at = token.expires_at.isoformat() + "Z"
        user_first_name = user.first_name
        user_display_name = user.display_name or user.first_name or email

    email_sent = lifecycle_emails.send_invite_email(
        user_id=user_id,
        email=email,
        raw_token=raw,
        first_name=user_first_name,
        org_name=(org_slug or "").strip() or None,
        token_id=token_id,
    )

    out = {
        "ok": True,
        "created": created,
        "user_id": user_id,
        "email": email,
        "display_name": user_display_name,
        "status": "invited",
        "invite_expires_at": expires_at,
        "email_sent": email_sent,
    }
    if membership is not None:
        out["membership"] = membership
    return out


def set_password_with_token(*, raw_token: str, new_password: str) -> Tuple[Optional[uuid.UUID], Optional[str]]:
    """Consume an invite token, set the password, activate the account.

    Returns (user_id, None) on success — caller issues the session envelope —
    or (None, error) with error ∈ token_invalid|token_expired|token_consumed|weak_password.
    """
    if not new_password or len(new_password) < MIN_PASSWORD_LEN:
        return None, "weak_password"

    from mobius_user.services.auth_service import get_auth_service

    auth = get_auth_service()

    with get_db_session() as session:
        token, err = _find_valid_token(session, raw_token, "invite")
        if err:
            return None, err

        user = session.query(AppUser).filter(AppUser.user_id == token.user_id).first()
        if not user:
            return None, "token_invalid"

        now = datetime.utcnow()
        user.password_hash = auth.hash_password(new_password)
        if user.status == "invited":
            user.status = "active"
        token.consumed_at = now

        has_email_link = (
            session.query(AuthProviderLink)
            .filter(
                AuthProviderLink.user_id == user.user_id,
                AuthProviderLink.provider == "email",
            )
            .first()
        )
        if not has_email_link:
            session.add(
                AuthProviderLink(user_id=user.user_id, provider="email", email=user.email)
            )

        session.commit()
        return user.user_id, None


def request_password_reset(*, tenant_id: uuid.UUID, email: str) -> None:
    """Fire-and-forget. Sends a reset email iff the user exists and is active.

    Deliberately returns nothing: the route answers 202 regardless, so the
    endpoint cannot be used to enumerate accounts.
    """
    email = (email or "").strip().lower()
    if not email:
        return

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
            logger.info("password_reset: no active user for requested email; skipping send")
            return

        raw, token = _issue_token(
            session,
            user_id=user.user_id,
            purpose="reset",
            ttl=_reset_ttl(),
            created_by="self",
        )
        session.commit()

        user_id = str(user.user_id)
        token_id = str(token.token_id)
        first_name = user.first_name

    lifecycle_emails.send_reset_email(
        user_id=user_id,
        email=email,
        raw_token=raw,
        first_name=first_name,
        token_id=token_id,
    )


def confirm_password_reset(*, raw_token: str, new_password: str) -> Optional[str]:
    """Consume a reset token, set the password, revoke all refresh sessions.

    Returns None on success, else token_invalid|token_expired|token_consumed|weak_password.
    """
    if not new_password or len(new_password) < MIN_PASSWORD_LEN:
        return "weak_password"

    from mobius_user.services.auth_service import get_auth_service

    auth = get_auth_service()

    with get_db_session() as session:
        token, err = _find_valid_token(session, raw_token, "reset")
        if err:
            return err

        user = session.query(AppUser).filter(AppUser.user_id == token.user_id).first()
        if not user:
            return "token_invalid"

        now = datetime.utcnow()
        user.password_hash = auth.hash_password(new_password)
        token.consumed_at = now

        # A reset means the old credential may be compromised — end every session.
        (
            session.query(UserSession)
            .filter(
                UserSession.user_id == user.user_id,
                UserSession.revoked_at.is_(None),
            )
            .update({UserSession.revoked_at: now}, synchronize_session=False)
        )

        session.commit()
        return None


def token_info(*, raw_token: str) -> dict:
    """Pre-flight for the set-password page: what is this link, is it usable."""
    with get_db_session() as session:
        token = (
            session.query(AuthToken)
            .filter(AuthToken.token_hash == hash_token(raw_token or ""))
            .first()
        )
        if not token:
            return {"valid": False, "error": "token_invalid"}

        user = session.query(AppUser).filter(AppUser.user_id == token.user_id).first()
        valid = token.is_valid and user is not None
        out = {
            "valid": valid,
            "purpose": token.purpose,
            "expires_at": token.expires_at.isoformat() + "Z",
            "email_masked": mask_email(user.email) if user else "•••",
        }
        if not valid:
            out["error"] = (
                "token_consumed" if token.consumed_at else "token_expired"
            )
        return out
