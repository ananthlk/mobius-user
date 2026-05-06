# mobius-user — integration spec

The contract any Mobius module (chat, rag, os extension, story-ui, future surfaces)
follows to consume identity, auth, and user preferences. Authoritative reference for
both backend (server-side) and frontend (browser-side) integration.

Version: **0.2.0** (Cloud Run revision tracks `Dockerfile` builds; service URL is stable).

---

## 1. Architecture in one picture

```
┌─────────────────┐         ┌──────────────────────────────────┐
│  Browser app    │         │  Module backend (chat/rag/etc.)  │
│  ─ @mobius/auth │         │                                   │
│  ─ AuthModal    │         │  Forwards /api/v1/auth/* via      │
│  ─ AuthService  │ ──XHR──▶│  thin proxy                        │
│                 │         │                                   │
└─────────────────┘         └──────────────┬───────────────────┘
                                            │
                                            ▼
                            ┌──────────────────────────────────┐
                            │       mobius-user                │
                            │  POST /api/v1/auth/{register,    │
                            │       login,google,refresh,...}  │
                            │  GET  /api/v1/auth/me            │
                            │  GET  /api/v1/public-config      │
                            └──────────────┬───────────────────┘
                                            │
                                            ▼
                            ┌──────────────────────────────────┐
                            │  Postgres (mobius_user DB)       │
                            └──────────────────────────────────┘
```

**Key invariant**: only `mobius-user` reads/writes the user database. Every other
module references users by `user_id` (UUID). No cross-DB foreign keys.

---

## 2. Service URLs

| Env | URL |
|---|---|
| dev (`mobius-os-dev`) | `https://mobius-user-ortabkknqa-uc.a.run.app` |
| prod | TBD (deploy via `scripts/deploy.sh prod` when `deploy/prod.env` exists) |

Resolve at runtime: `gcloud run services describe mobius-user --project=<proj> --region=us-central1 --format='value(status.url)'`.

---

## 3. Two integration patterns — pick one

### 3a. Proxy pattern (recommended)

Module backend forwards `/api/v1/auth/*` (and optionally `/api/v1/public-config`)
to mobius-user. Frontend talks to the module's own origin, no CORS needed.

**Why this is the default**: keeps the frontend `apiBase = window.origin/api/v1`
contract, which is what `@mobius/auth` ships out of the box. Lets module
backends stamp HTTPS, rate limits, audit logs, request IDs, etc. on auth
calls without touching mobius-user.

Full FastAPI proxy (~30 lines) in `mobius-chat/app/main.py` is the reference
implementation. Adapt for Flask / Express / Go / etc.

**Env vars on the proxying module**:
```
MOBIUS_USER_URL=https://mobius-user-ortabkknqa-uc.a.run.app
JWT_SECRET=<same secret mobius-user signs with>      # only if you also validate locally
```

### 3b. Direct pattern (browser → mobius-user)

Frontend calls mobius-user directly. Module backend not involved in auth.

**Use this when** the module doesn't have a backend (static site, extension
that talks to mobius-user from a content script), or when you want to
isolate the module from auth traffic.

**Caveats**:
- Requires `CORS_ALLOW_ORIGINS` on mobius-user to include the calling origin.
- Module backend can't observe auth calls (logging / rate limiting must
  happen on mobius-user).
- Frontend code must be configured with the mobius-user URL (extra
  bootstrap step vs. proxy pattern).

To configure `@mobius/auth` for direct mode:
```ts
const auth = createAuthService({
  apiBase: "https://mobius-user-ortabkknqa-uc.a.run.app/api/v1",
  storage: localStorageAdapter,
});
```

---

## 4. Endpoint reference

All paths under `/api/v1/auth/*` (plus the two service-level endpoints below).

### Service

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/health` | — | — | `{ok, service, version, google_sign_in, welcome_email}` | Liveness + feature flags |
| `GET` | `/api/v1/public-config` | — | — | `{google_client_id: string \| null}` | Frontend bootstrap. Safe to expose. |

### Auth

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/api/v1/auth/register` | — | `RegisterBody` | `AuthEnvelope + {is_new_user: true}` | Email/password. Auto-login. Triggers welcome email. |
| `POST` | `/api/v1/auth/login` | — | `LoginBody` | `AuthEnvelope` | Email/password. |
| `POST` | `/api/v1/auth/google` | — | `GoogleBody` | `AuthEnvelope + {is_new_user: bool}` | Verify-only ID-token flow. Auto-creates first-time. |
| `POST` | `/api/v1/auth/refresh` | — | `{refresh_token}` | `{ok, access_token, token_type, expires_in}` | New access token from refresh token. |
| `POST` | `/api/v1/auth/logout` | — | `{refresh_token}` | `{ok, message}` | Revokes the session row. |
| `POST` | `/api/v1/auth/check-email` | — | `{email, tenant_id?}` | `{exists: bool, user?}` | For page detection. |
| `GET` | `/api/v1/auth/me` | Bearer | — | `{ok, user}` | Authoritative profile. |

### User / preferences

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/api/v1/auth/activities` | — | — | `{ok, activities[]}` | List of onboarding activity options. |
| `PUT` | `/api/v1/auth/onboarding` | Bearer | `OnboardingBody` | `{ok, message}` | First-time setup. Sets `is_onboarded`. |
| `PUT` | `/api/v1/auth/preferences` | Bearer | `PreferencesBody` | `{ok, message}` | Post-onboarding edits. |

---

## 5. Request/response shapes

### `RegisterBody`
```json
{
  "email": "user@example.com",
  "password": "min-8-chars",
  "first_name": "Alice",      // optional
  "display_name": "Alice C",  // optional; defaults to email local-part
  "tenant_id": "uuid"         // optional; defaults to DEFAULT_TENANT_ID
}
```

### `LoginBody`
```json
{
  "email": "user@example.com",
  "password": "...",
  "tenant_id": "uuid",
  "device_info": {"ua": "...", "ip_hint": "..."}
}
```

### `GoogleBody`
```json
{
  "id_token": "<JWT from Google Identity Services>",
  "tenant_id": "uuid",
  "device_info": {"ua": "..."}
}
```
The frontend obtains `id_token` from `accounts.google.com/gsi/client` (the
`callback({credential})` value). Server-side mobius-user verifies the
signature against Google's JWKS, audience-checks `aud == GOOGLE_CLIENT_ID`,
and rejects unverified emails.

### `AuthEnvelope` (returned by register/login/google)
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

`is_new_user` semantics:
- `register`: always `true`.
- `google`: `true` only when there was no `auth_provider_link` row for `(google, sub)` AND no existing user with the Google email. If the email matched a pre-existing user (e.g., they previously signed up with email/password), the Google provider gets linked and `is_new_user = false`.
- `login`: not present (use `user.is_onboarded` for first-run UX gating instead).

### `OnboardingBody`
```json
{
  "preferred_name": "Alice",
  "activities": ["verify_eligibility", "submit_claims"],
  "ai_experience_level": "regular",
  "autonomy_routine_tasks": "automatic",
  "autonomy_sensitive_tasks": "confirm_first",
  "tone": "professional",
  "greeting_enabled": true,
  "timezone": "America/New_York"
}
```

### `PreferencesBody`
Subset of OnboardingBody — every field optional. Only fields present are updated.

### Error format

All error responses follow FastAPI's default:
```json
{"detail": "Human-readable error string"}
```

For `register` Bad Requests, `detail` is one of:
- `"Email is required"`, `"Password is required"`, `"Password must be at least 8 characters"`, `"Email already registered"`

For `login` 401s: `"Invalid email or password"` or `"Account uses OAuth login"`.

For `google` 401s: `"Google sign-in not configured (GOOGLE_CLIENT_ID missing)"`, `"Invalid Google ID token: <reason>"`, `"Google email is not verified"`, etc.

For Bearer-protected routes with no/bad token: `401 {"detail": "Unauthorized"}`.

---

## 6. JWT contract (for modules that validate locally)

If a module wants to authenticate requests without round-tripping
mobius-user (e.g., chat validates tokens to gate per-user rate limiting),
it can decode the access token directly. Required to share `JWT_SECRET`.

- **Algorithm**: `HS256`
- **Type claim**: `type: "access"` for access tokens, `type: "refresh"` for refresh tokens. Modules should reject any other.
- **Standard claims**: `sub` (user_id, UUID string), `tenant_id` (UUID string), `exp` (epoch seconds).

```python
# Python
import jwt
payload = jwt.decode(access_token, JWT_SECRET, algorithms=["HS256"])
if payload.get("type") != "access":
    raise Unauthorized()
user_id = payload["sub"]
tenant_id = payload["tenant_id"]
```

```ts
// Node
import jwt from "jsonwebtoken";
const payload = jwt.verify(token, JWT_SECRET, { algorithms: ["HS256"] }) as {
  sub: string;
  tenant_id: string;
  exp: number;
  type: "access" | "refresh";
};
if (payload.type !== "access") throw new Error("Unauthorized");
```

For the canonical user record (incl. activities + preferences), call
`GET /api/v1/auth/me` with `Authorization: Bearer <access_token>`.

**Token rotation**:
- Access token TTL = `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (default 60).
- Refresh token TTL = `JWT_REFRESH_TOKEN_EXPIRE_DAYS` (default 7).
- `@mobius/auth` AuthService schedules a refresh ~5 min before expiry; modules using their own clients should do the same.

---

## 7. Frontend envelope (`@mobius/auth` package)

Published as `@mobius/auth` (npm `file:` dep in the monorepo today; will be
published once stable). Single-source UI for all surfaces.

```ts
import {
  createAuthService,
  createAuthModal,
  createPreferencesModal,
  localStorageAdapter,           // web
  // createChromeStorageAdapter, // extension
  AUTH_STYLES,
} from "@mobius/auth";

// 1. AuthService — token storage + token refresh + emits login/logout events
const auth = createAuthService({
  apiBase: `${window.location.origin}/api/v1`,   // proxy pattern
  // apiBase: "https://mobius-user-...run.app/api/v1",  // direct pattern
  storage: localStorageAdapter,
});

// 2. AuthModal — login, signup, account-view, post-signup welcome
const modal = createAuthModal({
  auth,
  showOAuth: true,
  googleClientId: "<from /api/v1/public-config>",
  onSuccess: (user) => { /* host post-login wiring */ },
});

// 3. PreferencesModal — onboarding + post-onboarding edits
const prefsModal = createPreferencesModal(`${window.location.origin}/api/v1`, auth);

// 4. Bridge: AuthModal calls window.onOpenPreferences when the user clicks
//    "Set up preferences" on the welcome panel or "Preferences" in the
//    account view. Host wires it once.
(window as any).onOpenPreferences = () => prefsModal.open();

// 5. Mount once
document.body.appendChild(modal.el);
document.head.insertAdjacentHTML("beforeend", `<style>${AUTH_STYLES}</style>`);

// 6. Open on user click
sidebarUserButton.addEventListener("click", async () => {
  const profile = await auth.getUserProfile();
  modal.open(profile ? "account" : "login");
});
```

### What AuthService stores in localStorage / chrome.storage
```
mobius.auth.accessToken    string (JWT)
mobius.auth.refreshToken   string (JWT)
mobius.auth.expiresAt      number (Date.now()+expires_in*1000)
mobius.auth.userProfile    UserProfile JSON (cached for instant render)
```

### AuthService API

```ts
auth.login(email, password)              → { success, user?, error? }
auth.register(email, password, displayName?, firstName?)  → { success, user?, isNewUser?, error? }
auth.loginWithGoogle(idToken)            → { success, user?, isNewUser?, error? }
auth.logout()                            → void (revokes server session, clears local)
auth.refreshAccessToken()                → boolean
auth.getAccessToken()                    → string | null  (auto-refreshes if near-expiry)
auth.getCurrentUser()                    → UserProfile | null  (calls /me)
auth.getAuthState()                      → "unauthenticated" | "onboarding" | "authenticated"
auth.checkEmail(email)                   → { exists, user? }
auth.getAuthHeader()                     → { Authorization: "Bearer ..." } | null
auth.on(callback)                        → unsubscribe (events: login, logout, tokenRefreshed)
```

### Google sign-in client side (handled by AuthModal)

`AuthModal` does this transparently. For reference:

```ts
import { getGoogleIdToken } from "@mobius/auth";
const idToken = await getGoogleIdToken(googleClientId);   // popup; throws if dismissed
const result = await auth.loginWithGoogle(idToken);
if (result.isNewUser) showWelcomePanel();                 // first time
else showLoginToast();                                     // returning user
```

---

## 8. Public-config bootstrap

Frontends fetch this once at boot to get the Google Client ID (and any
future feature flags). Public, unauthenticated, designed for browsers.

```
GET /api/v1/public-config
→ {"google_client_id": "1032922478554-cis9...apps.googleusercontent.com"}
```

If the proxy module also exposes `/api/v1/public-config` (as chat does),
either point it at mobius-user or have it read its own `GOOGLE_CLIENT_ID`
env var. Same value either way; one source of truth is cleaner.

Frontend code pattern:
```ts
const cfg = await fetch(`${apiBase}/public-config`).then(r => r.json());
const modal = createAuthModal({ auth, googleClientId: cfg.google_client_id, ... });
```

---

## 9. Google sign-in flow (sequence)

```
Browser                        Proxy (chat)               mobius-user        Google
   │                                │                          │                 │
   │  click "Sign in with Google"   │                          │                 │
   │  GIS popup → user picks acct ─────────────────────────────────────────────▶ │
   │  ◀──────────────── id_token (JWT) ────────────────────────────────────────  │
   │                                │                          │                 │
   │  POST /api/v1/auth/google      │                          │                 │
   │  { id_token } ────────────────▶│                          │                 │
   │                                │  POST /api/v1/auth/google│                 │
   │                                │  forwarded ─────────────▶│                 │
   │                                │                          │  verify JWKS ──▶│
   │                                │                          │  ◀──────────────│
   │                                │                          │  find/create user
   │                                │                          │  issue access+refresh
   │                                │  ◀── AuthEnvelope ───────│                 │
   │  ◀──── AuthEnvelope ───────────│                          │                 │
   │  if is_new_user: welcome panel │                          │                 │
```

---

## 10. CORS contract

mobius-user's `CORS_ALLOW_ORIGINS` must include any browser origin that
calls it directly (pattern 3b). For the proxy pattern (3a), no CORS
config on mobius-user is needed — the browser only sees the proxy origin.

Defaults per env:

| Env | Origins allowlist |
|---|---|
| dev | `https://mobius-chat-ortabkknqa-uc.a.run.app, http://localhost:8000` |
| prod | (set when prod URLs are known) |

Methods: `GET, POST, PUT, DELETE, OPTIONS`. Headers: `Authorization, Content-Type, X-Requested-With`. Credentials: **off** (Bearer tokens, not cookies).

---

## 11. Welcome email

Best-effort POST to `${MOBIUS_EMAIL_SKILL_URL}/email/send` from mobius-user
after the new-user paths (`register` always; `google` when `is_new_user=true`).
Idempotent on `welcome:<user_id>`.

Failure never blocks signup — logged and the response still returns success.

---

## 12. Versioning

The `/api/v1/*` prefix is the public contract. Breaking changes go to
`/api/v2/*` and live alongside `v1` until consumers cut over.

The `version` field in `/health` reflects the build (release tag). Use it
for deploy verification, not for feature gating — feature flags belong
in `/api/v1/public-config`.

---

## 13. Per-module recipes

### mobius-chat (proxy pattern)

```bash
gcloud run services update mobius-chat \
  --project=mobius-os-dev --region=us-central1 \
  --update-env-vars=MOBIUS_OS_AUTH_URL=https://mobius-user-ortabkknqa-uc.a.run.app
```

Chat already has a forwarding proxy at `/api/v1/auth/{path:path}`. Renaming
the env var to `MOBIUS_AUTH_URL` is cosmetic; the existing one works.

### mobius-rag (proxy pattern, when added)

Same as chat: ~30 lines of FastAPI proxy + the env var. Frontend (if any)
gets `apiBase = window.origin/api/v1`.

### mobius-os Chrome extension (direct pattern)

Extensions don't have a backend to proxy through, so use the direct
pattern. Configure AuthService:

```ts
import { createAuthService, createChromeStorageAdapter } from "@mobius/auth";
const auth = createAuthService({
  apiBase: "https://mobius-user-ortabkknqa-uc.a.run.app/api/v1",
  storage: createChromeStorageAdapter(),
});
```

Add the extension origin (`chrome-extension://<id>`) to mobius-user's
`CORS_ALLOW_ORIGINS`.

### Server-only consumers (validate JWT, no UI)

Module receives `Authorization: Bearer <access_token>` from a frontend
call to itself, validates locally:

```python
import jwt, os
JWT_SECRET = os.getenv("JWT_SECRET")  # mounted from Secret Manager jwt-secret
def get_user_id(authorization: str) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    if payload.get("type") != "access":
        raise HTTPException(401)
    return payload["sub"]
```

If the module needs the full profile (display_name, preferences), call
`GET /api/v1/auth/me` once per session and cache.

---

## 14. Required env on mobius-user (deploy-time)

| Var | Source | Purpose |
|---|---|---|
| `USER_DATABASE_URL` | dev.env (template; password substituted at boot) | Postgres connection |
| `DB_PASSWORD` | Secret Manager `db-password` | Substituted into `USER_DATABASE_URL` |
| `JWT_SECRET` | Secret Manager `jwt-secret` | Token signing — must match consumers that validate locally |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | dev.env (default `60`) | |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | dev.env (default `7`) | |
| `DEFAULT_TENANT_ID` | dev.env | Used when callers don't pass `tenant_id` |
| `DEFAULT_TENANT_NAME` | dev.env | |
| `GOOGLE_CLIENT_ID` | dev.env (public) | Web-application OAuth client |
| `MOBIUS_EMAIL_SKILL_URL` | dev.env | Welcome email chokepoint |
| `MOBIUS_WELCOME_EMAIL_DISABLED` | env (optional `1`) | Hard-off switch |
| `CORS_ALLOW_ORIGINS` | dev.env (comma-separated) | Direct-pattern origins |

---

## 15. Open questions / known gaps

- **Multi-tenancy at the URL level**: today `tenant_id` is a body field; should it become a path prefix (`/api/v1/t/{tenant_id}/auth/...`) or a header (`X-Mobius-Tenant`)?
- **Email verification**: not built (deferred — see Mobius-user/INTEGRATION.md follow-ups).
- **Account deletion / GDPR delete**: no endpoint yet.
- **OAuth providers beyond Google**: Microsoft, Okta, generic OIDC are placeholders in the modal; backend support not yet built.
- **Prod deploy**: `deploy/prod.env` not yet committed. Add when prod URLs resolve.
