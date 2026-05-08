# Recipe — chat hamburger admin item ("Registered users")

For mobius-chat (or any host with a hamburger / drawer menu) that wants
to surface a "Registered users" view backed by mobius-user's admin API.
Pairs with [SPEC.md §4](SPEC.md#4-endpoint-reference) — see the admin
endpoints there.

---

## 1. What the API gives you

Two endpoints on mobius-user, both Bearer-authed AND email-allowlisted
(`MOBIUS_USER_ADMIN_EMAILS` env var on mobius-user — see §4).

```
GET  /api/v1/admin/users?limit=200&offset=0&q=optional-search-term
GET  /api/v1/admin/users/{user_id}
```

### `GET /admin/users` response
```json
{
  "ok": true,
  "users": [
    {
      "user_id": "uuid",
      "email": "alalithakumar@gmail.com",
      "first_name": "Ananth",
      "display_name": "Ananth L",
      "preferred_name": "Ananth",
      "is_onboarded": true,
      "created_at": "2026-05-06T12:48:55Z",
      "last_login_at": "2026-05-08T09:11:42Z",
      "auth_providers": ["email", "google"],
      "has_profile": true,
      "profile_version": 1
    }
  ],
  "total": 9,
  "limit": 200,
  "offset": 0
}
```

### `GET /admin/users/{id}` response
Single user with everything needed for a detail panel:
- All identity fields (`user_id`, `email`, `first_name`, `display_name`, etc.)
- `is_onboarded`, `onboarding_completed_at`
- `activities` — `[{activity_code, label, is_primary}]`
- `auth_providers` — `[{provider, email, linked_at}]`
- `active_sessions` — count of non-revoked sessions
- `preference` — full preference row
- `profile` — the full profile envelope, including `rendered_prompt`

---

## 2. Wiring chat — two pieces

### 2a. Backend: extend chat's existing auth proxy to also forward `/admin/*`

Chat's `app/main.py` already has a proxy at
`/api/v1/auth/{auth_path:path}` that forwards to `MOBIUS_OS_AUTH_URL`
(which is now the mobius-user URL). Add a parallel route for admin —
identical logic, different prefix:

```python
# app/main.py — add alongside the existing proxy_auth route
@app.api_route(
    "/api/v1/admin/{admin_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def proxy_admin(admin_path: str, request: Request):
    base = (os.getenv("MOBIUS_OS_AUTH_URL") or "").rstrip("/")
    if not base or "not-yet-deployed" in base:
        return FastAPIResponse(
            content='{"error":"admin not configured (MOBIUS_OS_AUTH_URL unset)"}',
            status_code=503,
            media_type="application/json",
        )
    target = f"{base}/api/v1/admin/{admin_path}"
    body = await request.body()

    forward_headers = {}
    for h in ("authorization", "content-type", "user-agent"):
        v = request.headers.get(h)
        if v:
            forward_headers[h] = v

    import httpx
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            up = await client.request(
                request.method,
                target,
                content=body if body else None,
                headers=forward_headers,
                params=dict(request.query_params),
            )
    except httpx.RequestError as exc:
        return FastAPIResponse(
            content=f'{{"error":"admin upstream unreachable: {type(exc).__name__}"}}',
            status_code=502,
            media_type="application/json",
        )

    response_headers = {}
    ct = up.headers.get("content-type")
    if ct:
        response_headers["content-type"] = ct
    return FastAPIResponse(
        content=up.content,
        status_code=up.status_code,
        headers=response_headers,
    )
```

That's a copy-paste of the existing `proxy_auth` with `/auth/` swapped
for `/admin/`. If you'd rather DRY it, refactor both into a single
shared helper — but two ~30-line routes is also fine.

### 2b. Frontend: hamburger menu item + modal

Add a new entry to the existing hamburger drawer that opens a modal
calling `${API_BASE}/api/v1/admin/users`. Pseudocode (TypeScript):

```ts
async function loadUsers(query?: string): Promise<AdminUserListResponse> {
  const url = `${API_BASE}/api/v1/admin/users?` +
    new URLSearchParams({ limit: "200", ...(query ? { q: query } : {}) });
  const token = await auth.getAccessToken();
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 403) throw new Error("Not in admin allowlist");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function loadUserDetail(userId: string): Promise<AdminUserDetailResponse> {
  const token = await auth.getAccessToken();
  const res = await fetch(`${API_BASE}/api/v1/admin/users/${userId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

For the modal/table, mobius-user ships a reference implementation in
[`Mobius-user/app/static/admin.html`](app/static/admin.html) — copy
the table + click-to-expand logic from there if you want a head start.
Or render with whatever component library chat already uses.

**Type imports** (TypeScript):
```ts
type AdminUser = {
  user_id: string;
  email: string | null;
  first_name: string | null;
  display_name: string | null;
  preferred_name: string | null;
  is_onboarded: boolean;
  created_at: string | null;
  last_login_at: string | null;
  auth_providers: string[];
  has_profile: boolean;
  profile_version: number | null;
};

type AdminUserDetail = {
  user_id: string;
  tenant_id: string;
  email: string | null;
  first_name: string | null;
  display_name: string | null;
  preferred_name: string | null;
  timezone: string | null;
  locale: string | null;
  avatar_url: string | null;
  is_onboarded: boolean;
  status: string;
  created_at: string | null;
  last_login_at: string | null;
  onboarding_completed_at: string | null;
  activities: Array<{activity_code: string; label: string; is_primary: boolean}>;
  auth_providers: Array<{provider: string; email: string | null; linked_at: string | null}>;
  active_sessions: number;
  preference: {
    tone: string;
    greeting_enabled: boolean;
    ai_experience_level: string;
    autonomy_routine_tasks: string;
    autonomy_sensitive_tasks: string;
  } | null;
  profile: UserProfile | null;  // see CONSUMER_RECIPE_PROFILE.md
};
```

### 2c. Hamburger menu placement

Add the entry only when the current user is in the admin allowlist.
Pattern: probe `/api/v1/admin/users?limit=1` once at app boot; if it
returns 200, render the menu item — if 403, hide it. Cache the
boolean for the session. Avoids confusing non-admin users with a
menu item they can't use.

```ts
let isAdmin: boolean | null = null;
async function checkAdmin(): Promise<boolean> {
  if (isAdmin !== null) return isAdmin;
  try {
    const token = await auth.getAccessToken();
    if (!token) { isAdmin = false; return false; }
    const res = await fetch(`${API_BASE}/api/v1/admin/users?limit=1`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    isAdmin = res.ok;
  } catch {
    isAdmin = false;
  }
  return isAdmin;
}

// On boot:
checkAdmin().then((ok) => {
  if (ok) drawer.appendChild(adminMenuItem);
});
```

---

## 3. Auth model

| Layer | Behavior |
|---|---|
| Bearer token | Same JWT chat already issues / refreshes. Reuse. |
| 401 | Token missing or invalid → trigger re-login (your existing flow). |
| 403 | User authenticated but not in `MOBIUS_USER_ADMIN_EMAILS` allowlist → hide UI, log a debug line. |

Add admin emails to mobius-user's deploy config:
```bash
gcloud run services update mobius-user \
  --project=mobius-os-dev --region=us-central1 \
  --update-env-vars=MOBIUS_USER_ADMIN_EMAILS=alalithakumar@gmail.com,someone-else@example.com
```

Or commit the value to `Mobius-user/deploy/dev.env` and redeploy via
`scripts/deploy.sh dev`.

---

## 4. UX guidance (recommended, not required)

- **Default sort**: most recent login first (the API returns `created_at desc`; sort client-side if you want last-login first).
- **Search**: debounced 200ms; pass `q=` to the API (server does case-insensitive ILIKE on email/first_name/preferred_name/display_name).
- **Detail panel**: lazy-load on row click; cache by `user_id` for the session.
- **Profile rendered_prompt**: show in a monospace `<pre>` block — it's third-person addressable to the LLM and reads like a system prompt.
- **Empty states**:
  - "User has not onboarded" → `profile == null`, `is_onboarded == false`. Show that explicitly so admin understands why the prompt is blank.
  - "Pre-existing user, no profile yet" → `is_onboarded == true, has_profile == false`. Should be rare (the backfill script catches these); flag for `regenerate_user_profile` follow-up.

---

## 5. What the standalone /admin page on mobius-user is for

mobius-user serves the same dashboard at `https://mobius-user-…run.app/admin`
(separate origin, separate localStorage). Use cases for keeping it:

- **Dev debugging**: works when chat is down or being redeployed.
- **Disaster recovery**: if chat's frontend has a bug, ops can still see who's signed up.
- **Cross-domain visibility**: useful from non-chat surfaces (extension, OS app) without each one having to wire its own admin UI.

Cost is zero (one HTML file + one route). Recommend keeping. Easy to remove later by deleting `app/static/admin.html` and the `/admin` route in `app/main.py`.

---

## 6. Future extensions (not v1)

- **Query history per user** — needs a chat-side endpoint
  `GET /internal/threads?user_id=X&limit=N` (chat owns the data).
  When ready: chat exposes it admin-gated, mobius-user's admin UI calls it.
- **Real role-based access** — replace `MOBIUS_USER_ADMIN_EMAILS` allowlist with `app_user.is_admin` boolean (DB) and link from the existing `role` table.
- **Activity feed** — last N actions per user (login, prefs update, etc.) — needs an audit log.
- **Bulk actions** — disable user, force-logout, reset password.
