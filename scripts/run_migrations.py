#!/usr/bin/env python3
"""
Run mobius-user Alembic migrations.

Usage:
    cd mobius-user
    USER_DATABASE_URL=postgresql://user:pass@localhost/mobius_user python scripts/run_migrations.py

Or with mobius-config:
    cd mobius-user
    ../../mobius-config/run_with_shared_env.sh . python scripts/run_migrations.py
"""

import os
import subprocess
import sys
from pathlib import Path

# Load .env from mobius-user or mobius-config
def load_env():
    root = Path(__file__).resolve().parent.parent
    env_file = root / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except ImportError:
            pass
    # Also try mobius-config
    config_env = root.parent / "mobius-config" / ".env"
    if config_env.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(config_env, override=False)
        except ImportError:
            pass


if __name__ == "__main__":
    load_env()

    if not os.getenv("USER_DATABASE_URL"):
        print("ERROR: USER_DATABASE_URL not set.", file=sys.stderr)
        print("Set it in .env or: USER_DATABASE_URL=postgresql://user:pass@host/mobius_user", file=sys.stderr)
        sys.exit(1)

    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(root),
    )
    sys.exit(result.returncode)
