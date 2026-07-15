"""Unit tests for the invite / set-password / reset primitives.

Covers the pure-logic layer (token crypto, masking, throttling, email
construction). DB-bound flows (create_or_reinvite_user, set_password_with_token,
confirm_password_reset) run against Postgres and are exercised by the
deploy smoke checks — they need the postgres-specific UUID/ARRAY columns
and have no sqlite equivalent.
"""
from __future__ import annotations

import re

import pytest

from mobius_user.services import account_lifecycle as al
from mobius_user.services import lifecycle_emails as le


# ── Token primitives ──────────────────────────────────────────────────────


def test_raw_tokens_are_unique_and_urlsafe():
    tokens = {al.generate_raw_token() for _ in range(200)}
    assert len(tokens) == 200
    for t in tokens:
        assert re.fullmatch(r"[A-Za-z0-9_-]+", t), "token must be URL-safe"
        assert len(t) >= 40, "token_urlsafe(32) yields ≥40 chars"


def test_hash_token_is_deterministic_sha256_hex():
    raw = al.generate_raw_token()
    h1, h2 = al.hash_token(raw), al.hash_token(raw)
    assert h1 == h2
    assert re.fullmatch(r"[0-9a-f]{64}", h1)
    assert al.hash_token("other") != h1


def test_raw_token_never_equals_stored_hash():
    raw = al.generate_raw_token()
    assert al.hash_token(raw) != raw


# ── Email masking ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "email,expected",
    [
        ("ananth@acme.com", "a•••@acme.com"),
        ("x@y.org", "x•••@y.org"),
        ("", "•••"),
        ("not-an-email", "•••"),
        ("@nodomain", "•••"),
    ],
)
def test_mask_email(email, expected):
    assert al.mask_email(email) == expected


# ── Throttle ──────────────────────────────────────────────────────────────


def test_throttle_allows_up_to_max_then_blocks():
    t = al._Throttle(max_events=3, window_seconds=60)
    assert t.allow("k")
    assert t.allow("k")
    assert t.allow("k")
    assert not t.allow("k")
    assert t.allow("other-key"), "keys are independent"


def test_throttle_window_expiry(monkeypatch):
    t = al._Throttle(max_events=1, window_seconds=10)
    clock = iter([100.0, 100.5, 111.0])
    monkeypatch.setattr(al.time, "monotonic", lambda: next(clock))
    assert t.allow("k")
    assert not t.allow("k")
    assert t.allow("k"), "event outside the window must not count"


# ── TTL configuration ─────────────────────────────────────────────────────


def test_ttl_defaults(monkeypatch):
    monkeypatch.delenv("MOBIUS_INVITE_TTL_DAYS", raising=False)
    monkeypatch.delenv("MOBIUS_RESET_TTL_MINUTES", raising=False)
    assert al._invite_ttl().days == 7
    assert al._reset_ttl().total_seconds() == 3600


def test_ttl_env_overrides(monkeypatch):
    monkeypatch.setenv("MOBIUS_INVITE_TTL_DAYS", "3")
    monkeypatch.setenv("MOBIUS_RESET_TTL_MINUTES", "15")
    assert al._invite_ttl().days == 3
    assert al._reset_ttl().total_seconds() == 900


# ── Email construction ────────────────────────────────────────────────────


def test_build_link_requires_base(monkeypatch):
    monkeypatch.delenv("MOBIUS_SET_PASSWORD_URL_BASE", raising=False)
    assert le.build_link("tok") is None
    monkeypatch.setenv("MOBIUS_SET_PASSWORD_URL_BASE", "https://app.example.com/set-password/")
    assert le.build_link("tok123") == "https://app.example.com/set-password?token=tok123"


def test_invite_body_contains_link_and_org():
    body = le._invite_body("Priya", "https://x/set?token=t", "acme-health")
    assert "https://x/set?token=t" in body
    assert "Priya" in body
    assert "acme-health" in body
    assert "used once" in body.lower() or "once" in body


def test_reset_body_contains_link_and_disclaimer():
    body = le._reset_body(None, "https://x/set?token=t")
    assert "https://x/set?token=t" in body
    assert "Hi there" in body
    assert "unchanged" in body, "must reassure the non-requester"


def test_sends_are_suppressed_when_disabled(monkeypatch):
    monkeypatch.setenv("MOBIUS_LIFECYCLE_EMAIL_DISABLED", "1")
    monkeypatch.setenv("MOBIUS_SET_PASSWORD_URL_BASE", "https://x/set")
    assert le.send_invite_email(user_id="u", email="a@b.c", raw_token="t") is False
    assert le.send_reset_email(user_id="u", email="a@b.c", raw_token="t") is False


def test_invite_email_skipped_without_link_base(monkeypatch):
    monkeypatch.delenv("MOBIUS_SET_PASSWORD_URL_BASE", raising=False)
    monkeypatch.delenv("MOBIUS_LIFECYCLE_EMAIL_DISABLED", raising=False)
    assert le.send_invite_email(user_id="u", email="a@b.c", raw_token="t") is False


# ── Password policy ───────────────────────────────────────────────────────


def test_set_password_rejects_weak_password_before_touching_db():
    user_id, err = al.set_password_with_token(raw_token="anything", new_password="short")
    assert user_id is None
    assert err == "weak_password"


def test_confirm_reset_rejects_weak_password_before_touching_db():
    assert al.confirm_password_reset(raw_token="anything", new_password="short") == "weak_password"


# ── Route wiring smoke ────────────────────────────────────────────────────


def test_route_modules_import_and_expose_new_paths():
    from mobius_user.routes import fastapi_auth, users

    auth_paths = {r.path for r in fastapi_auth.router.routes}
    assert "/set-password" in auth_paths
    assert "/password-reset/request" in auth_paths
    assert "/password-reset/confirm" in auth_paths
    assert "/token-info" in auth_paths

    user_paths = {r.path for r in users.router.routes}
    assert "/invite" in user_paths


def test_auth_token_model_registered():
    from mobius_user.models.tenant import AuthToken

    assert AuthToken.__tablename__ == "auth_token"
    cols = {c.name for c in AuthToken.__table__.columns}
    assert {"token_id", "user_id", "purpose", "token_hash", "expires_at", "consumed_at"} <= cols
