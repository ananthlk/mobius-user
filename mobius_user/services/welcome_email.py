"""Welcome email — best-effort send via the mobius-skills/email chokepoint.

Failure must never break signup. We log and move on.

Configuration:
- MOBIUS_EMAIL_SKILL_URL  Base URL of the email skill (e.g. https://mobius-email-...run.app)
- MOBIUS_WELCOME_EMAIL_DISABLED=1 to suppress sends in CI/dev
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

WELCOME_SUBJECT = "Welcome to Mobius"


def _welcome_body(first_name: Optional[str]) -> str:
    name = (first_name or "").strip() or "there"
    return (
        f"Hi {name},\n"
        "\n"
        "Thanks for signing up for Mobius.\n"
        "\n"
        "Mobius helps behavioral health teams turn their data into clear "
        "answers — credentialing, billing, and operations, all in one place. "
        "We're glad you're here.\n"
        "\n"
        "If you didn't sign up, you can ignore this email.\n"
        "\n"
        "— The Mobius team\n"
    )


def send_welcome_email(
    *,
    user_id: str,
    email: str,
    first_name: Optional[str] = None,
) -> bool:
    """POST to the email skill's /email/send chokepoint. Returns True on success."""
    if os.getenv("MOBIUS_WELCOME_EMAIL_DISABLED") == "1":
        logger.info("welcome_email: disabled by MOBIUS_WELCOME_EMAIL_DISABLED")
        return False

    base = (os.getenv("MOBIUS_EMAIL_SKILL_URL") or "").rstrip("/")
    if not base:
        logger.warning("welcome_email: MOBIUS_EMAIL_SKILL_URL not set; skipping")
        return False
    if not email:
        return False

    try:
        import requests
    except ImportError:
        logger.warning("welcome_email: 'requests' not installed; skipping")
        return False

    payload = {
        "to": [email],
        "subject": WELCOME_SUBJECT,
        "body": _welcome_body(first_name),
        "sender": "system",
        "idempotency_key": f"welcome:{user_id}",
        "actor": "system:mobius_user",
        "intent": "welcome_signup",
        "mode": "raw",
    }
    try:
        res = requests.post(f"{base}/email/send", json=payload, timeout=5)
        if res.status_code >= 300:
            logger.warning("welcome_email: skill returned %s: %s", res.status_code, res.text[:200])
            return False
        data = res.json() if res.content else {}
        if not data.get("sent"):
            logger.warning("welcome_email: not sent: %s", data.get("error") or data)
            return False
        from mobius_user.services.account_lifecycle import mask_email
        logger.info("welcome_email: sent to %s (message_id=%s)", mask_email(email), data.get("message_id"))
        return True
    except Exception as exc:
        logger.warning("welcome_email: send failed: %s", exc)
        return False
