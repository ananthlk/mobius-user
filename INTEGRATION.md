# Mobius-user — integration envelope

Standalone auth + user service. Anything that needs sign-in (mobius-chat,
mobius-os extension, mstart, future surfaces) consumes it.

## Service URL

```
https://mobius-user-ortabkknqa-uc.a.run.app
```

(Resolve current URL: `gcloud run services describe mobius-user --project=mobius-os-dev --region=us-central1 --format='value(status.url)'`)

## Endpoints

All paths are prefixed with `/api/v1/auth/*` (plus a couple of unauthed surface endpoints).

| Method | Path | Body | Auth | Purpose |
|---|---|---|---|---|
| GET | `/health` | — | none | Service health probe (returns version, feature flags) |
| GET | `/api/v1/public-config` | — | none | `{"google_client_id": "<id-or-null>"}` for frontends to bootstrap GIS |
| POST | `/api/v1/auth/register` | `{email, password, first_name?, display_name?, tenant_id?}` | none | Create account + auto-login. Sends welcome email best-effort. Returns `{ok, is_new_user, access_token, refresh_token, user}` |
| POST | `/api/v1/auth/login` | `{email, password, tenant_id?, device_info?}` | none | Email/password login. Returns same envelope as register |
| POST | `/api/v1/auth/google` | `{id_token, tenant_id?, device_info?}` | none | Verify Google ID token (server-side via JWKS), find-or-create user. Returns `{ok, is_new_user, access_token, refresh_token, user}` |
| POST | `/api/v1/auth/refresh` | `{refresh_token}` | none | Issue a new access token |
| POST | `/api/v1/auth/logout` | `{refresh_token}` | none | Revoke session |
| GET | `/api/v1/auth/me` | — | Bearer | Current user profile + activities + preference |
| PUT | `/api/v1/auth/onboarding` | `{preferred_name, activities, ai_experience_level, autonomy_routine_tasks, autonomy_sensitive_tasks, tone, greeting_enabled, timezone}` | Bearer | First-time preferences setup |
| POST | `/api/v1/auth/check-email` | `{email, tenant_id?}` | none | `{exists, user?}` — for page detection / EHR pages |
| GET | `/api/v1/auth/activities` | — | none | List of activity options for onboarding |
| PUT | `/api/v1/auth/preferences` | `{preferred_name?, timezone?, locale?, tone?, greeting_enabled?, autonomy_routine_tasks?, autonomy_sensitive_tasks?, activities?}` | Bearer | Update prefs post-onboarding |

**Bearer auth**: `Authorization: Bearer <access_token>`. Tokens are JWTs signed with `JWT_SECRET` (HS256), expire in 60 min by default.

## Envelope shape for register / login / google

```json
{
  "ok": true,
  "is_new_user": false,
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "user_id": "uuid",
    "tenant_id": "uuid",
    "email": "user@example.com",
    "display_name": "Alice",
    "first_name": "Alice",
    "preferred_name": null,
    "is_onboarded": false,
    "activities": [],
    "preference": null
  }
}
```

`is_new_user` is `true` for `/register` always, and for `/google` when the Google ID was not previously linked to any account (sign-up path).

## JWT format

- **Algorithm**: HS256
- **Secret**: `JWT_SECRET` (Secret Manager `jwt-secret`). Consumers that validate tokens MUST share this secret.
- **Access token claims**: `sub` (user_id), `tenant_id`, `exp`, `type: "access"`
- **Refresh token claims**: `sub` (user_id), `session_id`, `exp`, `type: "refresh"`

If a consumer wants to validate access tokens locally (no roundtrip to mobius-user):

```python
import jwt
payload = jwt.decode(access_token, JWT_SECRET, algorithms=["HS256"])
assert payload["type"] == "access"
user_id = payload["sub"]
```

For the canonical user record, hit `GET /api/v1/auth/me` with the Bearer token.

## What chat needs to wire

**Backend (one-line proxy, no app code change):** simplest path is to
forward `/api/v1/auth/*` to mobius-user. The proxy chat already has (added
in `c80baac`) does this when `MOBIUS_OS_AUTH_URL` is set — just rename it
or reuse it pointing at mobius-user:

```bash
gcloud run services update mobius-chat \
  --project=mobius-os-dev --region=us-central1 \
  --update-env-vars=MOBIUS_OS_AUTH_URL=https://<mobius-user-url>
```

(Or rename the env var to `MOBIUS_AUTH_URL` if you prefer — the chat-side
proxy code reads it as `MOBIUS_OS_AUTH_URL` today; rename is a one-line
change in `app/main.py`.)

Chat ALSO exposes its own `/api/v1/public-config` endpoint with the Google
Client ID baked in — once chat starts proxying to mobius-user, you can
either:
- Keep chat's `/api/v1/public-config` (it reads its own `GOOGLE_CLIENT_ID` env)
- Or redirect/proxy `/api/v1/public-config` to mobius-user as well so there's
  one source of truth (recommended)

**Frontend:** no changes. The `@mobius/auth` AuthService is configured with
`apiBase = window.origin/api/v1`. Once chat proxies, all calls land on
mobius-user transparently.

## Env vars on mobius-user (Cloud Run)

| Var | Value | Source |
|---|---|---|
| `USER_DATABASE_URL` | `postgresql+psycopg://postgres:__DB_PASSWORD__@/mobius_user?host=/cloudsql/...` | `deploy/dev.env` (template; password substituted at boot) |
| `JWT_SECRET` | (32+ char random) | Secret Manager `jwt-secret` |
| `DB_PASSWORD` | (Cloud SQL postgres password) | Secret Manager `db-password` |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | dev.env |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | dev.env |
| `DEFAULT_TENANT_ID` | `00000000-0000-0000-0000-000000000001` | dev.env |
| `DEFAULT_TENANT_NAME` | `Default Tenant` | dev.env |
| `GOOGLE_CLIENT_ID` | `1032922478554-cis9...apps.googleusercontent.com` | dev.env |
| `MOBIUS_EMAIL_SKILL_URL` | `https://mobius-email-ortabkknqa-uc.a.run.app` | dev.env |
| `CORS_ALLOW_ORIGINS` | `https://mobius-chat-ortabkknqa-uc.a.run.app,http://localhost:8000` | dev.env |

## Database

- **Cloud SQL instance**: `mobius-os-dev:us-central1:mobius-platform-dev-db`
- **Database name**: `mobius_user` (separate from `mobius_os` and `mobius_chat`)
- **Schema**: created by alembic — `tenant`, `role`, `app_user`, `auth_provider_link`, `user_session`, `activity`, `user_activity`, `user_preference`, `alembic_version`
- **Run migrations**: from a machine with cloud-sql-proxy (or local mstart) on `127.0.0.1:5433`:
  ```bash
  cd Mobius-user
  USER_DATABASE_URL='postgresql://postgres:<pw>@127.0.0.1:5433/mobius_user' alembic upgrade head
  ```

## Deploy

```bash
cd /Users/ananth/Mobius/Mobius-user
scripts/deploy.sh dev                  # build + deploy + smoke
scripts/deploy.sh dev --dry-run        # see commands without running
scripts/deploy.sh dev --skip-build     # redeploy last image
scripts/deploy.sh dev --skip-smoke     # skip post-deploy probes
```

The deploy script reads `deploy/dev.env`, builds via `gcloud builds submit
--config=deploy/cloudbuild.yaml`, and `gcloud run deploys` with all env
vars + secrets.

## Local dev

```bash
cd Mobius-user
USER_DATABASE_URL='postgresql://postgres:MobiusDev123$@127.0.0.1:5433/mobius_user' \
JWT_SECRET=dev-jwt-secret-change-in-production \
GOOGLE_CLIENT_ID=1032922478554-cis9qh077pak9r98kp599g84ia5fbou5.apps.googleusercontent.com \
MOBIUS_EMAIL_SKILL_URL=http://localhost:8013 \
CORS_ALLOW_ORIGINS='*' \
uvicorn app.main:app --port 8002 --reload
```

(Suggest a different local port from chat's 8000 and rag's 8001.)

## Welcome email

Best-effort POST to `${MOBIUS_EMAIL_SKILL_URL}/email/send` after first
signup (email/password and Google new-user paths). Failure logs a warning
but never breaks signup. Idempotent on `welcome:<user_id>` so re-tries
won't double-send.

If the email skill's Gmail OAuth token expires, welcome emails silently
skip — re-bootstrap via `mobius-skills/email/scripts/oauth_bootstrap.py`
and update Secret Manager `mobius-email-gmail-token`.

## Cutover plan from mobius-os auth

1. Deploy mobius-user (this).
2. Migrate any production users from `mobius_os.app_user` to `mobius_user.app_user` (alembic data migration — TBD when needed).
3. Update chat's `MOBIUS_OS_AUTH_URL` to point at mobius-user URL.
4. Remove `app/routes/auth.py` + `app/services/auth_service.py` + `app/services/welcome_email.py` from mobius-os (separate PR after verifying chat works against mobius-user for ~a week).
5. (Eventually) drop the `mobius_os.app_user` tables.

For dev, no migration needed — `mobius_user` DB starts fresh.
