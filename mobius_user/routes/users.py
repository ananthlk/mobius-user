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

try:
    from fastapi import APIRouter, HTTPException, Query, Request
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
    import re

    return re.sub(r"(^-+|-+$)", "", re.sub(r"[^a-z0-9]+", "-", value.strip().lower()))


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
            return resp.json(), True
        except ValueError:
            logger.warning("Master org registry returned non-JSON for %s", org_slug)
            return None, False
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


def _memberships(session, user_id) -> list[dict]:
    rows = (
        session.query(UserOrgMembership)
        .filter(UserOrgMembership.user_id == user_id)
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


def _profile(session, user: AppUser) -> dict:
    memberships = _memberships(session, user.user_id)
    aliases = (
        session.query(UserAlias).filter(UserAlias.user_id == user.user_id).all()
    )
    return {
        **_candidate(user),
        "status": user.status,
        "org_memberships": memberships,
        "roles_by_org": {m["org_slug"]: m["roles"] for m in memberships},
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
            # Accept a canonical slug or a display name — slugify either way.
            org_members = {
                m.user_id
                for m in session.query(UserOrgMembership)
                .filter(
                    UserOrgMembership.user_id.in_(list(by_id)),
                    UserOrgMembership.org_slug == _slugify(org),
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

        return {
            "ok": True,
            "user": {
                **_candidate(user),
                "org_memberships": _memberships(session, user.user_id),
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

    master_org, reachable = _master_org_lookup(slug)
    if reachable and master_org is None:
        # Direct slug miss — the caller may have passed a display name or
        # alias; let the master's free-text resolver have a shot before
        # rejecting ("David Lawrence Center for Behavioral Health" should
        # land on the canonical slug, not 422).
        resolved = _master_org_resolve(org_slug.strip())
        if resolved is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown org_slug '{slug}' in master org registry",
            )
        slug = resolved["org_slug"]
        master_org = {"org_name": resolved.get("display_name")}
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
            row.updated_at = datetime.utcnow()
        else:
            session.add(
                UserOrgMembership(
                    user_id=uid, org_slug=slug, org_display_name=display_name, roles=roles
                )
            )
        session.commit()
        return {
            "ok": True,
            "user_id": str(uid),
            "org_slug": slug,
            "display_name": display_name,
            "roles": roles,
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


def _existing_user_id(user_id: str) -> uuid.UUID:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown user")
    with get_db_session() as session:
        if not session.query(AppUser.user_id).filter(AppUser.user_id == uid).first():
            raise HTTPException(status_code=404, detail="Unknown user")
    return uid
