# mobius-user

Shared user/auth module for Mobius applications. Owns its own PostgreSQL database (`mobius_user`) and provides authentication, user profiles, and preferences that can be consumed by mobius-os, mobius-chat, and other modules.

## Overview

- **Database:** `mobius_user` (PostgreSQL)
- **Tables:** tenant, role, app_user, auth_provider_link, user_session, activity, user_activity, user_preference
- **Services:** AuthService (JWT, bcrypt, register, login, validate), UserContextService (UserProfile)
- **Routes:** Flask Blueprint and FastAPI router for auth endpoints

Each Mobius module (mobius-os, mobius-chat) has its own database; mobius-user is a separate DB. Cross-references use `user_id` (UUID) only—no cross-DB foreign keys.

## Installation

```bash
# From Mobius repo root
pip install -e ./mobius-user

# With optional Flask/FastAPI support
pip install -e "mobius-user[flask,fastapi,migrations]"
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `USER_DATABASE_URL` | PostgreSQL URL (e.g. `postgresql://user:pass@localhost:5432/mobius_user`) |
| `JWT_SECRET` | Secret for JWT signing (must match across all apps sharing auth) |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifetime (default: 60) |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token lifetime (default: 7) |
| `DEFAULT_TENANT_ID` | Default tenant UUID for development |
| `DEFAULT_TENANT_NAME` | Default tenant name |

## Setup

### 1. Create the database

```bash
psql -U postgres -c "CREATE DATABASE mobius_user;"
# Or use scripts/create_db.sql
```

### 2. Run migrations

```bash
cd mobius-user
USER_DATABASE_URL=postgresql://user:pass@localhost:5432/mobius_user python scripts/run_migrations.py
# Or: USER_DATABASE_URL=... alembic upgrade head
```

### 3. Use mobius-config (optional)

Add to `mobius-config/.env`:

```
USER_DATABASE_URL=postgresql://postgres:password@localhost:5432/mobius_user
JWT_SECRET=your-secret-key-change-in-production
```

Then run migrations:

```bash
cd mobius-config
./run_with_shared_env.sh ../mobius-user python scripts/run_migrations.py
```

## Usage

### As a library (mobius-os, mobius-chat)

```python
# Initialize (call once at app startup)
from mobius_user.db import init_db
import os

init_db(os.getenv("USER_DATABASE_URL"))

# Auth service
from mobius_user import get_auth_service

auth_service = get_auth_service()
user, error = auth_service.register_user(tenant_id, email, password)
auth_response, error = auth_service.authenticate_email(email, password, tenant_id)
user = auth_service.validate_access_token(access_token)

# User context
from mobius_user import get_user_context_service

ctx = get_user_context_service()
profile = ctx.get_user_profile(user_id)
```

### Flask (mobius-os)

```python
from mobius_user.db import init_db
from mobius_user.routes.flask_auth import bp as auth_bp

init_db(os.getenv("USER_DATABASE_URL"))
app.register_blueprint(auth_bp)
# Routes: /api/v1/auth/register, /login, /refresh, /logout, /me, /onboarding, etc.
```

### FastAPI (mobius-chat)

```python
from mobius_user.db import init_db
from mobius_user.routes.fastapi_auth import router as auth_router

init_db(os.getenv("USER_DATABASE_URL"))
app.include_router(auth_router, prefix="/api/v1/auth")
```

## Auth Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /register | Create account (email/password) |
| POST | /login | Login |
| POST | /refresh | Refresh access token |
| POST | /logout | Invalidate session |
| GET | /me | Current user profile |
| PUT | /onboarding | Complete onboarding |
| POST | /check-email | Check if email exists |
| GET | /activities | List activities |
| PUT | /preferences | Update preferences |

## Data Migration (from mobius-os)

To migrate existing user data from mobius-os's `mobius` DB to `mobius_user`:

1. Create mobius_user DB and run migrations
2. Run a one-time migration script (to be added) that copies tenant, app_user, auth_provider_link, user_session, activity, user_activity, user_preference
3. Update mobius-os to use mobius-user library and USER_DATABASE_URL
4. Remove user tables from mobius-os migrations (or drop after verification)

## Project Structure

```
mobius-user/
├── mobius_user/
│   ├── models/       # SQLAlchemy models
│   ├── services/     # AuthService, UserContextService
│   ├── routes/       # Flask Blueprint, FastAPI router
│   └── db/           # Session factory
├── migrations/       # Alembic
├── scripts/          # run_migrations.py, create_db.sql
├── .env.example
├── pyproject.toml
└── README.md
```
