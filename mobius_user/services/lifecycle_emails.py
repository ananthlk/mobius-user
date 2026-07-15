"""Invite / password-reset emails — best-effort via the mobius-skills/email chokepoint.

Same contract as welcome_email.py: failure must never break the calling flow.
The raw token appears ONLY in these links — callers must not log it.

Configuration:
- MOBIUS_EMAIL_SKILL_URL          Base URL of the email skill
- MOBIUS_SET_PASSWORD_URL_BASE    Page that consumes ?token=… links
                                  (one page serves both purposes; it calls
                                  GET /api/v1/auth/token-info to render).
- MOBIUS_LIFECYCLE_EMAIL_DISABLED=1 to suppress sends in CI/dev
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

INVITE_SUBJECT = "You've been invited to Mobius"
RESET_SUBJECT = "Reset your Mobius password"


def build_link(raw_token: str) -> Optional[str]:
    base = (os.getenv("MOBIUS_SET_PASSWORD_URL_BASE") or "").rstrip("/")
    if not base:
        return None
    return f"{base}?token={raw_token}"


def _invite_body(first_name: Optional[str], link: str, org_name: Optional[str]) -> str:
    name = (first_name or "").strip() or "there"
    org_line = f" to join {org_name}" if org_name else ""
    return (
        f"Hi {name},\n"
        "\n"
        f"You've been invited{org_line} on Mobius.\n"
        "\n"
        "Set your password to activate your account:\n"
        f"{link}\n"
        "\n"
        "This link is valid for a limited time and can be used once. "
        "If you weren't expecting this invitation, you can ignore this email.\n"
        "\n"
        "— The Mobius team\n"
    )


def _reset_body(first_name: Optional[str], link: str) -> str:
    name = (first_name or "").strip() or "there"
    return (
        f"Hi {name},\n"
        "\n"
        "We received a request to reset your Mobius password. "
        "Use the link below to choose a new one:\n"
        f"{link}\n"
        "\n"
        "This link expires shortly and can be used once. "
        "If you didn't request this, you can ignore this email — "
        "your password is unchanged.\n"
        "\n"
        "— The Mobius team\n"
    )


def _send(
    *,
    email: str,
    subject: str,
    body: str,
    idempotency_key: str,
    intent: str,
) -> bool:
    if os.getenv("MOBIUS_LIFECYCLE_EMAIL_DISABLED") == "1":
        logger.info("lifecycle_email: disabled by MOBIUS_LIFECYCLE_EMAIL_DISABLED")
        return False

    base = (os.getenv("MOBIUS_EMAIL_SKILL_URL") or "").rstrip("/")
    if not base:
        logger.warning("lifecycle_email: MOBIUS_EMAIL_SKILL_URL not set; skipping")
        return False
    if not email:
        return False

    try:
        import requests
    except ImportError:
        logger.warning("lifecycle_email: 'requests' not installed; skipping")
        return False

    payload = {
        "to": [email],
        "subject": subject,
        "body": body,
        "sender": "system",
        "idempotency_key": idempotency_key,
        "actor": "system:mobius_user",
        "intent": intent,
        "mode": "raw",
    }
    try:
        res = requests.post(f"{base}/email/send", json=payload, timeout=5)
        if res.status_code >= 300:
            logger.warning(
                "lifecycle_email: skill returned %s: %s", res.status_code, res.text[:200]
            )
            return False
        data = res.json() if res.content else {}
        if not data.get("sent"):
            logger.warning("lifecycle_email: not sent: %s", data.get("error") or data)
            return False
        logger.info(
            "lifecycle_email: %s sent to %s (message_id=%s)",
            intent,
            email,
            data.get("message_id"),
        )
        return True
    except Exception as exc:
        logger.warning("lifecycle_email: send failed: %s", exc)
        return False


def send_invite_email(
    *,
    user_id: str,
    email: str,
    raw_token: str,
    first_name: Optional[str] = None,
    org_name: Optional[str] = None,
    token_id: Optional[str] = None,
) -> bool:
    link = build_link(raw_token)
    if not link:
        logger.warning("invite_email: MOBIUS_SET_PASSWORD_URL_BASE not set; skipping")
        return False
    return _send(
        email=email,
        subject=INVITE_SUBJECT,
        body=_invite_body(first_name, link, org_name),
        # Keyed on token_id, not user_id: a re-invite mints a new token and
        # must not be deduplicated away by the email skill.
        idempotency_key=f"invite:{token_id or user_id}",
        intent="account_invite",
    )


def send_reset_email(
    *,
    user_id: str,
    email: str,
    raw_token: str,
    first_name: Optional[str] = None,
    token_id: Optional[str] = None,
) -> bool:
    link = build_link(raw_token)
    if not link:
        logger.warning("reset_email: MOBIUS_SET_PASSWORD_URL_BASE not set; skipping")
        return False
    return _send(
        email=email,
        subject=RESET_SUBJECT,
        body=_reset_body(first_name, link),
        idempotency_key=f"reset:{token_id or user_id}",
        intent="password_reset",
    )
