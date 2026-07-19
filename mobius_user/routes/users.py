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
    UserCapability,
    UserOrgMembership,
    UserSession,
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


# ── Master org registry (provider-roster-credentialing) ──────────────────
#
# Ownership decision (Ananth, 2026-07-08): the roster/credentialing service
# owns the master org registry; mobius-user consumes canonical org_slugs.


def _master_org_url() -> str:
    return (
        os.getenv("MOBIUS_ROSTER_URL")
        or "https://mobius-provider-roster-credentialing-ortabkknqa-uc.a.run.app"
    ).rstrip("/")


def _slugify(value: str) -> str:
    # Underscores are preserved: payor org slugs use the lexicon underscore
    # convention (sunshine_health) while customer/internal/system slugs use
    # the org-master hyphen convention — the slug encodes which authority
    # owns the org, so we must not translate between the two.
    import re

    return re.sub(r"(^-+|-+$)", "", re.sub(r"[^a-z0-9_]+", "-", value.strip().lower()))


def _master_org_lookup(org_slug: str) -> tuple[Optional[dict], bool]:
    """Look up a slug in the master registry.

    Returns (org, reachable): org is None when the master definitively does
    not know the slug; reachable=False means the master couldn't be asked —
    callers accept the write unvalidated rather than blocking enrollment on
    a master outage.
    """
    import requests

    try:
        resp = requests.get(f"{_master_org_url()}/org/{org_slug}", timeout=3)
    except requests.RequestException as exc:
        logger.warning("Master org registry unreachable for %s: %s", org_slug, exc)
        return None, False
    if resp.status_code == 404:
        return None, True
    if resp.ok:
        try:
            org = resp.json()
        except ValueError:
            logger.warning("Master org registry returned non-JSON for %s", org_slug)
            return None, False
        # Tombstones (merged/quarantined rows) still 200 on direct GET but
        # must not validate an enrollment — treat as a definitive miss so
        # the /resolve fallback chases to the canonical target instead.
        if org.get("status") in ("merged", "quarantined"):
            return None, True
        return org, True
    logger.warning("Master org registry returned %s for %s", resp.status_code, org_slug)
    return None, False


def _master_org_resolve(name: str) -> Optional[dict]:
    """Free-text → canonical org via the master's POST /org/resolve.

    Returns {org_slug, display_name, matched_via} or None (unknown name,
    endpoint not deployed yet, or master unreachable — callers already have
    a definitive-404 answer from the direct lookup, so None just means the
    fallback couldn't improve on it).
    """
    import requests

    try:
        resp = requests.post(
            f"{_master_org_url()}/org/resolve", json={"name": name}, timeout=3
        )
    except requests.RequestException as exc:
        logger.warning("Master /org/resolve unreachable for %r: %s", name, exc)
        return None
    if not resp.ok:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if body.get("org_slug") else None


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


def _memberships(session, user_id, status: str = "active") -> list[dict]:
    """Membership rows at the given status.

    Every consumer that treats membership as trust (org_memberships in
    profile/by-identity/me, directory, resolve boost, instant-RAG org-tier
    filtering) reads ACTIVE only — a pending self-claim must never behave
    like membership.
    """
    rows = (
        session.query(UserOrgMembership)
        .filter(
            UserOrgMembership.user_id == user_id,
            UserOrgMembership.status == status,
        )
        .all()
    )
    return [
        {
            "org_slug": m.org_slug,
            "display_name": m.org_display_name or m.org_slug,
            "roles": list(m.roles or []),
        }
        for m in rows
    ]


def _welcome_block(session, user: AppUser, pref, memberships: list[dict], pending: list[dict] | None = None) -> dict:
    """Tailored-onboarding contract (docs/welcome-onboarding-spec.md §2).

    Always present; server-computed; chat decides when to render. Welcome
    content REFERENCES behavior the profile prompt ENFORCES — this block
    carries the targeting signals only, never instructions.
    """
    from mobius_user.models.tenant import AuthToken
    from mobius_user.models.activity import Activity, UserActivity

    # First session = the user has at most one session row (the one that
    # authenticated this request). Sturdier than last_login_at timing.
    session_count = (
        session.query(UserSession.session_id)
        .filter(UserSession.user_id == user.user_id)
        .limit(2)
        .count()
    )

    invited = (
        session.query(AuthToken.token_id)
        .filter(AuthToken.user_id == user.user_id, AuthToken.purpose == "invite")
        .first()
        is not None
    )

    activity_rows = (
        session.query(UserActivity, Activity)
        .join(Activity, Activity.activity_id == UserActivity.activity_id)
        .filter(UserActivity.user_id == user.user_id)
        .all()
    )
    activity_rows.sort(key=lambda t: (not t[0].is_primary, t[1].display_order))

    roles: list[str] = sorted({r for m in memberships for r in m["roles"]})

    return {
        "first_session": session_count <= 1,
        "is_onboarded": user.is_onboarded,
        "arrival": "invited" if invited else "self_serve",
        "org_status": (
            "member" if memberships else ("pending" if pending else "none")
        ),
        "roles": roles,
        "activities": [a.activity_code for _, a in activity_rows],
        "experience_level": (pref.ai_experience_level if pref else None) or "beginner",
        "tone": (pref.tone if pref else None) or "professional",
    }


def _capabilities(session, user_id) -> list[dict]:
    """Active authority grants — ADMIN-set, read-only on every profile
    surface. org_slug None = global."""
    rows = (
        session.query(UserCapability)
        .filter(
            UserCapability.user_id == user_id,
            UserCapability.revoked_at.is_(None),
        )
        .all()
    )
    return [{"capability": c.capability, "org_slug": c.org_slug} for c in rows]


def _profile(session, user: AppUser) -> dict:
    memberships = _memberships(session, user.user_id)
    aliases = (
        session.query(UserAlias).filter(UserAlias.user_id == user.user_id).all()
    )
    return {
        **_candidate(user),
        "status": user.status,
        "org_memberships": memberships,
        "pending_org_memberships": _memberships(session, user.user_id, status="pending"),
        "roles_by_org": {m["org_slug"]: m["roles"] for m in memberships},
        "aliases": [a.alias for a in aliases],
        "capabilities": _capabilities(session, user.user_id),
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
            # Accept a canonical slug or a display name — slugify either way.
            org_members = {
                m.user_id
                for m in session.query(UserOrgMembership)
                .filter(
                    UserOrgMembership.user_id.in_(list(by_id)),
                    UserOrgMembership.org_slug == _slugify(org),
                    UserOrgMembership.status == "active",
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


@router.get("/directory")
def org_directory(
    request: Request,
    org_slug: str = Query(..., min_length=1, max_length=255),
    q: Optional[str] = Query(None, max_length=255, description="Optional typeahead filter"),
    limit: int = Query(20, ge=1, le=100),
):
    """Org-scoped member directory — powers @-mention autocomplete.

    Empty q returns the whole org roster (the default list when the user
    has just typed '@'); q narrows by name/email prefix-then-substring.
    Unlike /resolve, org_slug here is a hard filter: coworker mention
    lists must not leak users from other orgs.
    """
    _require_reader(request)
    slug = _slugify(org_slug)

    with get_db_session() as session:
        rows = (
            session.query(AppUser, UserOrgMembership)
            .join(UserOrgMembership, UserOrgMembership.user_id == AppUser.user_id)
            .filter(
                UserOrgMembership.org_slug == slug,
                UserOrgMembership.status == "active",
                AppUser.status == "active",
            )
            .all()
        )

        needle = (q or "").strip().lower()

        def rank(user: AppUser) -> int:
            if not needle:
                return 0
            best = -1
            for name in (
                user.display_name,
                user.first_name,
                user.preferred_name,
                user.email,
            ):
                lowered = (name or "").lower()
                if not lowered:
                    continue
                if lowered.startswith(needle):
                    best = max(best, 2)
                elif needle in lowered:
                    best = max(best, 1)
            return best

        members = []
        for user, membership in rows:
            score = rank(user)
            if needle and score < 0:
                continue
            members.append((score, (user.display_name or "").lower(), user, membership))
        members.sort(key=lambda t: (-t[0], t[1]))

        return {
            "ok": True,
            "org_slug": slug,
            "members": [
                {**_candidate(u), "roles": list(m.roles or [])}
                for _, _, u, m in members[:limit]
            ],
            "total": len(members),
        }


@router.get("/org/{org_slug}/members")
def org_members(
    request: Request,
    org_slug: str,
    limit: int = Query(200, ge=1, le=500),
):
    """Full org roster INCLUDING not-yet-activated accounts — admin surface.

    Powers the org-agent dashboard's total/invited/active tracker. Unlike
    /directory (mention lists — active-only trust semantics, deliberately
    unchanged), this returns every membership at any status with both the
    account status (invited|active|disabled) and the membership status
    (active|pending). Writer-gated: roster composition is admin data.
    """
    _require_writer(request)
    slug = _slugify(org_slug)
    with get_db_session() as session:
        rows = (
            session.query(AppUser, UserOrgMembership)
            .join(UserOrgMembership, UserOrgMembership.user_id == AppUser.user_id)
            .filter(UserOrgMembership.org_slug == slug)
            .order_by(AppUser.display_name)
            .limit(limit)
            .all()
        )
        return {
            "ok": True,
            "org_slug": slug,
            "members": [
                {
                    "user_id": str(u.user_id),
                    "display_name": u.display_name,
                    "email": u.email,
                    "is_agent": bool(u.is_agent),
                    "roles": list(m.roles or []),
                    "account_status": u.status,
                    "membership_status": m.status,
                }
                for u, m in rows
            ],
        }


@router.get("/pending-memberships")
def list_pending_memberships(
    request: Request,
    org_slug: Optional[str] = Query(None, max_length=255),
    limit: int = Query(50, ge=1, le=200),
):
    """Approval queue: self-claimed memberships awaiting a decision.

    Writer-gated — this is an admin surface (org-agent approval UI).
    """
    _require_writer(request)
    with get_db_session() as session:
        q = (
            session.query(UserOrgMembership, AppUser)
            .join(AppUser, AppUser.user_id == UserOrgMembership.user_id)
            .filter(UserOrgMembership.status == "pending")
        )
        if org_slug:
            q = q.filter(UserOrgMembership.org_slug == _slugify(org_slug))
        rows = q.order_by(UserOrgMembership.created_at).limit(limit).all()
        return {
            "ok": True,
            "pending": [
                {
                    "user_id": str(u.user_id),
                    "display_name": u.display_name,
                    "email": u.email,
                    "org_slug": m.org_slug,
                    "org_display_name": m.org_display_name,
                    "claimed_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m, u in rows
            ],
        }


@router.post("/{user_id}/orgs/{org_slug}/approve")
def approve_membership(request: Request, user_id: str, org_slug: str):
    """Approve a pending self-claimed membership (idempotent on active)."""
    _require_writer(request)
    uid = _existing_user_id(user_id)
    slug = _slugify(org_slug)
    approver = None
    bearer = _bearer_user(request)
    if bearer:
        approver = bearer.email or str(bearer.user_id)

    with get_db_session() as session:
        row = (
            session.query(UserOrgMembership)
            .filter(
                UserOrgMembership.user_id == uid,
                UserOrgMembership.org_slug == slug,
            )
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown membership")
        already = row.status == "active"
        if not already:
            row.status = "active"
            row.approved_by = approver or "internal-key"
            row.approved_at = datetime.utcnow()
            row.updated_at = datetime.utcnow()
            session.commit()
        return {
            "ok": True,
            "user_id": str(uid),
            "org_slug": slug,
            "status": "active",
            "already_active": already,
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

        # Greeting contract for chat's whoami: name to greet with + whether
        # the user wants a greeting at all (user_preference.greeting_enabled,
        # default true). Riding on by-identity keeps chat at one call per
        # pageload.
        from mobius_user.models.preference import UserPreference

        pref = (
            session.query(UserPreference)
            .filter(UserPreference.user_id == user.user_id)
            .first()
        )
        memberships = _memberships(session, user.user_id)
        pending = _memberships(session, user.user_id, status="pending")
        return {
            "ok": True,
            "user": {
                **_candidate(user),
                "org_memberships": memberships,
                "pending_org_memberships": pending,
                "greeting": {
                    "name": user.greeting_name,
                    "enabled": pref.greeting_enabled if pref else True,
                },
                "welcome": _welcome_block(session, user, pref, memberships, pending),
                "capabilities": _capabilities(session, user.user_id),
            },
        }


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


@router.put("/{user_id}/orgs/{org_slug}")
def upsert_membership(request: Request, user_id: str, org_slug: str, body: MembershipBody):
    """Enroll a user into an org by canonical slug.

    The slug must exist in the master org registry (provider-roster-
    credentialing). Definitively unknown → 422; master unreachable → the
    write is accepted unvalidated (validated:false) so enrollment never
    hard-depends on the master being up.
    """
    _require_writer(request)
    uid = _existing_user_id(user_id)
    slug = _slugify(org_slug)
    if not slug or org_slug.strip().startswith("_"):
        # _shared_ / _payor_registry_ are system scopes, not orgs
        raise HTTPException(status_code=422, detail="Invalid org slug")

    # Resolve-first: POST /org/resolve returns the CANONICAL slug (chases
    # merges, maps aliases), so a hand-slugified display name can never
    # land a membership on a duplicate variant slug — the exact failure
    # that split david-lawrence-center into two orgs. Direct GET is only
    # the degraded path when the resolver is unavailable.
    resolved = _master_org_resolve(org_slug.strip())
    if resolved is not None:
        slug = resolved["org_slug"]
        master_org: Optional[dict] = {"org_name": resolved.get("display_name")}
    else:
        master_org, reachable = _master_org_lookup(slug)
        if reachable and master_org is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown org_slug '{slug}' in master org registry",
            )
    display_name = (master_org or {}).get("org_name") or slug

    roles = sorted({r.strip() for r in body.roles if r.strip()})
    with get_db_session() as session:
        row = (
            session.query(UserOrgMembership)
            .filter(
                UserOrgMembership.user_id == uid,
                UserOrgMembership.org_slug == slug,
            )
            .first()
        )
        if row:
            row.roles = roles
            row.org_display_name = display_name
            # Admin PUT doubles as approval: a pending self-claim touched by
            # an admin grant activates.
            if row.status != "active":
                row.status = "active"
                row.approved_by = "admin-grant"
                row.approved_at = datetime.utcnow()
            row.updated_at = datetime.utcnow()
        else:
            session.add(
                UserOrgMembership(
                    user_id=uid,
                    org_slug=slug,
                    org_display_name=display_name,
                    roles=roles,
                    status="active",
                    approved_by="admin-grant",
                    approved_at=datetime.utcnow(),
                )
            )
        session.commit()
        return {
            "ok": True,
            "user_id": str(uid),
            "org_slug": slug,
            "display_name": display_name,
            "roles": roles,
            "status": "active",
            "validated": bool(master_org),
        }


@router.delete("/{user_id}/orgs/{org_slug}")
def remove_membership(request: Request, user_id: str, org_slug: str):
    _require_writer(request)
    uid = _existing_user_id(user_id)
    with get_db_session() as session:
        deleted = (
            session.query(UserOrgMembership)
            .filter(
                UserOrgMembership.user_id == uid,
                UserOrgMembership.org_slug == _slugify(org_slug),
            )
            .delete()
        )
        session.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail="Unknown membership")
        return {"ok": True}


class CapabilityBody(BaseModel):
    capability: str = Field(..., min_length=1, max_length=50)
    org_slug: Optional[str] = Field(None, max_length=255, description="Omit for a global grant")


@router.post("/{user_id}/capabilities")
def grant_capability(request: Request, user_id: str, body: CapabilityBody):
    """ADMIN-only authority grant (Ananth's ruling: admin-level set, never
    user enablement — deliberately not on the preferences PUT path).
    Idempotent on an existing active grant."""
    _require_writer(request)
    uid = _existing_user_id(user_id)
    cap = body.capability.strip().lower()
    org = _slugify(body.org_slug) if body.org_slug else None
    granter = None
    bearer = _bearer_user(request)
    if bearer:
        granter = bearer.email or str(bearer.user_id)

    with get_db_session() as session:
        existing = (
            session.query(UserCapability)
            .filter(
                UserCapability.user_id == uid,
                UserCapability.capability == cap,
                UserCapability.org_slug.is_(None) if org is None else UserCapability.org_slug == org,
                UserCapability.revoked_at.is_(None),
            )
            .first()
        )
        if existing:
            return {"ok": True, "granted": False, "already_active": True,
                    "capability": cap, "org_slug": org}
        session.add(
            UserCapability(
                user_id=uid, capability=cap, org_slug=org,
                granted_by=granter or "internal-key",
            )
        )
        session.commit()
        return {"ok": True, "granted": True, "capability": cap, "org_slug": org}


@router.delete("/{user_id}/capabilities/{capability}")
def revoke_capability(
    request: Request,
    user_id: str,
    capability: str,
    org_slug: Optional[str] = Query(None, max_length=255),
):
    """ADMIN-only revocation — stamps revoked_by/at, never deletes (the
    grant/revoke history IS the audit)."""
    _require_writer(request)
    uid = _existing_user_id(user_id)
    cap = capability.strip().lower()
    org = _slugify(org_slug) if org_slug else None
    revoker = None
    bearer = _bearer_user(request)
    if bearer:
        revoker = bearer.email or str(bearer.user_id)

    with get_db_session() as session:
        row = (
            session.query(UserCapability)
            .filter(
                UserCapability.user_id == uid,
                UserCapability.capability == cap,
                UserCapability.org_slug.is_(None) if org is None else UserCapability.org_slug == org,
                UserCapability.revoked_at.is_(None),
            )
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="No active grant")
        row.revoked_by = revoker or "internal-key"
        row.revoked_at = datetime.utcnow()
        session.commit()
        return {"ok": True, "revoked": True, "capability": cap, "org_slug": org}


def _existing_user_id(user_id: str) -> uuid.UUID:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown user")
    with get_db_session() as session:
        if not session.query(AppUser.user_id).filter(AppUser.user_id == uid).first():
            raise HTTPException(status_code=404, detail="Unknown user")
    return uid
