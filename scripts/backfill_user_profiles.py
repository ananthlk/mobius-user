"""One-shot backfill: regenerate user.profile for every user that has none.

Idempotent. Runs the same logic the live service uses (lazy regen on /me),
just proactively against every user. Safe to re-run.

Usage:
    cd Mobius-user
    USER_DATABASE_URL='postgresql://user:pass@127.0.0.1:5433/mobius_user' \\
        python scripts/backfill_user_profiles.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make package importable when running from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mobius_user.db.session import init_db, get_db_session  # noqa: E402
from mobius_user.models.tenant import AppUser  # noqa: E402
from mobius_user.models.preference import UserPreference  # noqa: E402
from mobius_user.services.auth_service import get_auth_service  # noqa: E402
from mobius_user.services.prompt_builder import CURRENT_TEMPLATE_VERSION  # noqa: E402


def main() -> int:
    db_url = os.getenv("USER_DATABASE_URL")
    if not db_url:
        print("ERROR: USER_DATABASE_URL not set.", file=sys.stderr)
        return 2
    init_db(db_url)

    svc = get_auth_service()

    # Find every active user. We don't filter by "missing profile" upfront —
    # regenerate_user_profile is idempotent, and stale-version users (when
    # the template ever bumps) get refreshed too.
    with get_db_session() as session:
        users = (
            session.query(AppUser)
            .filter(AppUser.status == "active")
            .order_by(AppUser.created_at.desc())
            .all()
        )
        user_rows = [(u.user_id, u.email or "(no email)") for u in users]

    backfilled = 0
    skipped_no_pref = 0
    already_current = 0

    for user_id, email in user_rows:
        # Peek at current state cheaply — avoid regenerating if already current.
        with get_db_session() as session:
            pref = (
                session.query(UserPreference)
                .filter(UserPreference.user_id == user_id)
                .first()
            )
            if pref is None:
                skipped_no_pref += 1
                print(f"  · skip (no pref row)        {email}")
                continue
            if (
                pref.profile_json
                and pref.profile_version == CURRENT_TEMPLATE_VERSION
            ):
                already_current += 1
                print(f"  · skip (already current v{pref.profile_version})  {email}")
                continue

        envelope = svc.regenerate_user_profile(user_id)
        if envelope:
            backfilled += 1
            preview = (envelope.get("rendered_prompt") or "")[:80].replace("\n", " ")
            print(f"  ✓ regenerated v{envelope.get('version')}  {email}  — {preview}…")
        else:
            print(f"  · skip (regen returned None) {email}")

    print()
    print(
        f"Done. backfilled={backfilled} already_current={already_current} "
        f"skipped_no_pref={skipped_no_pref} total={len(user_rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
