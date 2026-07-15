"""FastAPI router for the identity directory.

Mount with: app.include_router(users_router, prefix="/api/v1/users")

Contract consumer: mobius-task-manager (assignee identity mapping).
Read routes:

  GET /resolve?q=sam[&org=...][&limit=N]   ranked candidates for NL assignment
  GET /by-identity?subject=<sub>           JWT sub / provider id / email -> canonical user
  GET /{user_id}                           full directory profile

Assignee format contract: humans are ``user:{user_id}``, agents keep the
grandfathered ``agent:{name}`` (stored in app_user.canonical_handle). Every
response carries a pre-formatted ``assignee_ref`` so consumers never build
the string themselves.

Write routes (enrollment/management) are gated harder than reads — internal
key or admin allowlist:

  POST   /agents                           enroll an agent principal
  POST   /{user_id}/aliases                add an alias
  DELETE /{user_id}/aliases/{alias}
  PUT    /{user_id}/orgs/{org_name}        upsert membership + roles
  DELETE /{user_id}/orgs/{org_name}

Auth: reads accept a valid Bearer access token OR X-Internal-Key matching
MOBIUS_USER_INTERNAL_KEY (service-to-service). Writes accept the internal
key or an admin-allowlisted Bearer user (MOBIUS_USER_ADMIN_EMAILS).
"""
from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from mobius_user.db.session import get_db_session
from mobius_user.models.tenant import (
    AppUser,
    AuthProviderLink,
    UserAlias,
    UserOrgMembership,
)
from mobius_user.services.auth_service import get_auth_service, get_user_from_token
from mobius_user.services import account_lifecycle

try:
    from fastapi import APIRouter, HTTPException, Query, Request
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError(
        "FastAPI is required for user routes. Install with: pip install mobius-user[fastapi]"
    )

logger = logging.getLogger(__name__)

router = APIRouter(tags=["users"])


# ── Auth gates ────────────────────────────────────────────────────────────


def _internal_key_ok(request: Request) -> bool:
    expected = (os.getenv("MOBIUS_USER_INTERNAL_KEY") or "").strip()
    if not expected:
        return False  # unset = internal-key path disabled, never fall open
    provided = (request.headers.get("X-Internal-Key") or "").strip()
    return bool(provided) and secrets.compare_digest(provided, expected)


def _bearer_user(request: Request) -> Optional[AppUser]:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return get_user_from_token(auth_header[7:])


def _require_reader(request: Request) -> None:
    if _internal_key_ok(request):
        return
    if _bearer_user(request):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def _admin_allowlist() -> set[str]:
    raw = os.getenv("MOBIUS_USER_ADMIN_EMAILS") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _require_writer(request: Request) -> None:
    if _internal_key_ok(request):
        return
    user = _bearer_user(request)
    if user and (user.email or "").strip().lower() in _admin_allowlist():
        return
    raise HTTPException(status_code=403, detail="Forbidden")


# ── Serialization ─────────────────────────────────────────────────────────


def _candidate(user: AppUser) -> dict:
    return {
        "user_id": str(user.user_id),
        "display_name": user.display_name or user.preferred_name or user.first_name,
        "email": user.email,
        "is_agent": bool(user.is_agent),
        "assignee_ref": user.assignee_ref,
    }


def _profile(session, user: AppUser) -> dict:
    memberships = (
        session.query(UserOrgMembership)
        .filter(UserOrgMembership.user_id == user.user_id)
        .all()
    )
    aliases = (
        session.query(UserAlias).filter(UserAlias.user_id == user.user_id).all()
    )
    return {
        **_candidate(user),
        "status": user.status,
        "org_memberships": [m.org_name for m in memberships],
        "roles_by_org": {m.org_name: list(m.roles or []) for m in memberships},
        "aliases": [a.alias for a in aliases],
    }


# ── Read routes ───────────────────────────────────────────────────────────


@router.get("/resolve")
def resolve_users(
    request: Request,
    q: str = Query(..., min_length=1, max_length=255),
    org: Optional[str] = Query(None, description="Ranking boost (not a filter) for members of this org"),
    limit: int = Query(10, ge=1, le=50),
):
    """Ranked candidate resolution for natural-language assignment.

    ``org`` boosts members of that org but never hard-filters — incomplete
    membership data must not hide the only right answer.
    """
    _require_reader(request)
    needle = q.strip().lower()
    like = f"%{needle}%"

    with get_db_session() as session:
        users = (
            session.query(AppUser)
            .filter(AppUser.status == "active")
            .filter(
                (AppUser.display_name.ilike(like))
                | (AppUser.first_name.ilike(like))
                | (AppUser.preferred_name.ilike(like))
                | (AppUser.email.ilike(like))
                | (AppUser.canonical_handle.ilike(like))
            )
            .all()
        )
        by_id = {u.user_id: u for u in users}

        alias_rows = (
            session.query(UserAlias)
            .filter(UserAlias.alias.ilike(like))
            .all()
        )
        alias_hits: dict[uuid.UUID, UserAlias] = {}
        for row in alias_rows:
            if row.user_id not in by_id:
                hit = (
                    session.query(AppUser)
                    .filter(AppUser.user_id == row.user_id, AppUser.status == "active")
                    .first()
                )
                if not hit:
                    continue
                by_id[hit.user_id] = hit
            existing = alias_hits.get(row.user_id)
            if existing is None or row.weight > existing.weight:
                alias_hits[row.user_id] = row

        org_members: set[uuid.UUID] = set()
        if org and by_id:
            org_members = {
                m.user_id
                for m in session.query(UserOrgMembership)
                .filter(
                    UserOrgMembership.user_id.in_(list(by_id)),
                    UserOrgMembership.org_name == org,
                )
                .all()
            }

        def score(user: AppUser) -> int:
            names = [
                (user.display_name or ""),
                (user.first_name or ""),
                (user.preferred_name or ""),
                (user.email or ""),
                (user.canonical_handle or ""),
            ]
            alias = alias_hits.get(user.user_id)
            if alias:
                names.append(alias.alias)
            best = 0
            for name in names:
                lowered = name.lower()
                if not lowered:
                    continue
                if lowered == needle:
                    best = max(best, 100)
                elif lowered.startswith(needle):
                    best = max(best, 60)
                elif needle in lowered:
                    best = max(best, 30)
            if alias:
                best += alias.weight
            if user.user_id in org_members:
                best += 20
            return best

        ranked = sorted(by_id.values(), key=score, reverse=True)[:limit]
        return {
            "ok": True,
            "query": q,
            "candidates": [_candidate(u) for u in ranked],
        }


@router.get("/by-identity")
def resolve_by_identity(
    request: Request,
    subject: str = Query(..., min_length=1, max_length=255),
):
    """Map an authenticated subject to the canonical user.

    Resolution order: app_user.user_id (chat JWT ``sub`` is already this
    UUID) -> auth_provider_link.provider_user_id -> email. 404 when unknown
    — never guess; callers treat 404 as "unscoped fallback".
    """
    _require_reader(request)
    subject = subject.strip()

    with get_db_session() as session:
        user: Optional[AppUser] = None

        try:
            user = (
                session.query(AppUser)
                .filter(AppUser.user_id == uuid.UUID(subject))
                .first()
            )
        except ValueError:
            pass

        if user is None:
            link = (
                session.query(AuthProviderLink)
                .filter(AuthProviderLink.provider_user_id == subject)
                .first()
            )
            if link:
                user = (
                    session.query(AppUser)
                    .filter(AppUser.user_id == link.user_id)
                    .first()
                )

        if user is None and "@" in subject:
            user = (
                session.query(AppUser)
                .filter(AppUser.email.ilike(subject), AppUser.status == "active")
                .first()
            )

        if user is None or user.status != "active":
            raise HTTPException(status_code=404, detail="Unknown identity")

        return {"ok": True, "user": _candidate(user)}


@router.get("/{user_id}")
def get_user_profile(request: Request, user_id: str):
    """Full directory profile — display resolution for task cards."""
    _require_reader(request)
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown user")

    with get_db_session() as session:
        user = session.query(AppUser).filter(AppUser.user_id == uid).first()
        if user is None:
            raise HTTPException(status_code=404, detail="Unknown user")
        return {"ok": True, "user": _profile(session, user)}


# ── Write routes (enrollment / management) ────────────────────────────────


class AgentEnrollBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Agent handle; stored as agent:{name}")
    display_name: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=255)


@router.post("/agents")
def enroll_agent(request: Request, body: AgentEnrollBody):
    """Enroll (or fetch) an agent principal.

    Idempotent on canonical_handle — re-enrolling an existing agent returns
    the existing row so callers can enroll-on-startup safely.
    """
    _require_writer(request)
    name = body.name.strip().lower().removeprefix("agent:")
    if not name:
        raise HTTPException(status_code=422, detail="Agent name required")
    handle = f"agent:{name}"

    tenant = get_auth_service().get_or_create_default_tenant()
    with get_db_session() as session:
        existing = (
            session.query(AppUser)
            .filter(AppUser.canonical_handle == handle)
            .first()
        )
        if existing:
            return {"ok": True, "created": False, "user": _profile(session, existing)}

        user = AppUser(
            tenant_id=tenant.tenant_id,
            display_name=body.display_name or name,
            email=body.email,
            is_agent=True,
            canonical_handle=handle,
            status="active",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        logger.info("Enrolled agent principal %s (%s)", handle, user.user_id)
        return {"ok": True, "created": True, "user": _profile(session, user)}


class InviteBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    first_name: Optional[str] = Field(None, max_length=100)
    display_name: Optional[str] = Field(None, max_length=255)
    org_slug: Optional[str] = Field(None, max_length=255)
    roles: Optional[list[str]] = None
    invited_by: Optional[str] = Field(None, max_length=255)


@router.post("/invite")
def invite_user(request: Request, body: InviteBody):
    """Admin-create a pending employee account and email a set-password link.

    Org-agent onboarding contract (2026-07-15). Idempotent for accounts
    still in status=invited: re-inviting rotates the token, extends the
    expiry, and resends the email. Existing active/disabled accounts
    return 409 email_exists.
    """
    _require_writer(request)
    tenant = get_auth_service().get_or_create_default_tenant()

    result = account_lifecycle.create_or_reinvite_user(
        tenant_id=tenant.tenant_id,
        email=body.email,
        first_name=body.first_name,
        display_name=body.display_name,
        org_slug=body.org_slug,
        roles=body.roles,
        invited_by=body.invited_by,
    )

    if not result.get("ok"):
        if result.get("error") == "email_exists":
            raise HTTPException(status_code=409, detail=result)
        raise HTTPException(status_code=422, detail=result.get("error", "invalid"))

    status_code = 201 if result.get("created") else 200
    return JSONResponse(status_code=status_code, content=result)


class AliasBody(BaseModel):
    alias: str = Field(..., min_length=1, max_length=255)
    weight: int = Field(0, ge=-100, le=100)


@router.post("/{user_id}/aliases")
def add_alias(request: Request, user_id: str, body: AliasBody):
    _require_writer(request)
    uid = _existing_user_id(user_id)
    alias = body.alias.strip()
    if not alias:
        raise HTTPException(status_code=422, detail="Alias required")

    with get_db_session() as session:
        existing = (
            session.query(UserAlias)
            .filter(UserAlias.user_id == uid, UserAlias.alias == alias)
            .first()
        )
        if existing:
            existing.weight = body.weight
        else:
            session.add(UserAlias(user_id=uid, alias=alias, weight=body.weight))
        session.commit()
        return {"ok": True, "user_id": str(uid), "alias": alias}


@router.delete("/{user_id}/aliases/{alias}")
def remove_alias(request: Request, user_id: str, alias: str):
    _require_writer(request)
    uid = _existing_user_id(user_id)
    with get_db_session() as session:
        deleted = (
            session.query(UserAlias)
            .filter(UserAlias.user_id == uid, UserAlias.alias == alias)
            .delete()
        )
        session.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail="Unknown alias")
        return {"ok": True}


class MembershipBody(BaseModel):
    roles: list[str] = Field(default_factory=list, max_length=50)


@router.put("/{user_id}/orgs/{org_name}")
def upsert_membership(request: Request, user_id: str, org_name: str, body: MembershipBody):
    _require_writer(request)
    uid = _existing_user_id(user_id)
    org = org_name.strip()
    if not org or org.startswith("_"):
        # _shared_ / _payor_registry_ are system scopes, not orgs
        raise HTTPException(status_code=422, detail="Invalid org name")

    roles = sorted({r.strip() for r in body.roles if r.strip()})
    with get_db_session() as session:
        row = (
            session.query(UserOrgMembership)
            .filter(
                UserOrgMembership.user_id == uid,
                UserOrgMembership.org_name == org,
            )
            .first()
        )
        if row:
            row.roles = roles
            row.updated_at = datetime.utcnow()
        else:
            session.add(UserOrgMembership(user_id=uid, org_name=org, roles=roles))
        session.commit()
        return {"ok": True, "user_id": str(uid), "org_name": org, "roles": roles}


@router.delete("/{user_id}/orgs/{org_name}")
def remove_membership(request: Request, user_id: str, org_name: str):
    _require_writer(request)
    uid = _existing_user_id(user_id)
    with get_db_session() as session:
        deleted = (
            session.query(UserOrgMembership)
            .filter(
                UserOrgMembership.user_id == uid,
                UserOrgMembership.org_name == org_name,
            )
            .delete()
        )
        session.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail="Unknown membership")
        return {"ok": True}


def _existing_user_id(user_id: str) -> uuid.UUID:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown user")
    with get_db_session() as session:
        if not session.query(AppUser.user_id).filter(AppUser.user_id == uid).first():
            raise HTTPException(status_code=404, detail="Unknown user")
    return uid
