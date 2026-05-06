# Recipe — using `user.profile` in agent prompts

For chat, rag, or any LLM-backed module that wants to tailor responses
per user. Pairs with [SPEC.md §5a](SPEC.md). Read this once and you're
done.

---

## 30-second version

```python
# 1. Get the profile (already in /me — zero extra round trips).
profile = me_response["user"]["profile"]   # may be None for un-onboarded users

# 2. Splice into your system prompt.
system_prompt = (
    f"{BASE_SYSTEM_PROMPT}\n\n"
    f"{profile['rendered_prompt']}" if profile else BASE_SYSTEM_PROMPT
)

# 3. Send to the LLM.
```

That's it. The `rendered_prompt` is a 4–6 line block ready to drop in
between your base system prompt and the conversation. mobius-user
regenerates it whenever the user updates preferences — you don't need
to track changes.

---

## Where you already have it

If your service authenticates incoming requests with the JWT and then
calls `GET /api/v1/auth/me`, the response already contains the profile:

```json
{
  "ok": true,
  "user": {
    "user_id": "uuid",
    "first_name": "Sarah",
    "is_onboarded": true,
    "activities": [...],
    "preference": {...},
    "profile": {                              ← this
      "preferred_name": "Sarah",
      "tasks": [{"code": "verify_eligibility", "label": "Verify eligibility"}, ...],
      "communication": {"tone": "professional", "ai_experience_level": "regular", "greeting_enabled": true},
      "autonomy": {"routine_tasks": "automatic", "sensitive_tasks": "confirm_first"},
      "timezone": "America/New_York",
      "rendered_prompt": "The user's name is Sarah. They focus on...",
      "version": 1,
      "generated_at": "2026-05-06T14:31:19+00:00"
    }
  }
}
```

`profile` is `null` when the user has just signed up but hasn't onboarded
yet. Treat that as "no preferences known — use base system prompt only."

---

## Two integration patterns

### Pattern A — drop in `rendered_prompt` (recommended)

Simplest. One line. The string is designed to read naturally between a
base system prompt and a conversation:

```python
def build_system_prompt(base: str, profile: dict | None) -> str:
    if not profile:
        return base
    return f"{base}\n\n{profile['rendered_prompt']}"
```

When the user updates preferences, mobius-user re-renders. Next time
you call `/me`, you get the new string. No diffing, no re-templating.

### Pattern B — structured fields (fine-grained control)

Use when you need to gate UI behavior or tool calls on specific
preferences (not just prompt text):

```python
def autonomy_for(profile: dict | None, sensitive: bool) -> str:
    if not profile:
        return "confirm_first"
    return profile["autonomy"]["sensitive_tasks" if sensitive else "routine_tasks"]

# Example: chat decides whether to render a "Confirm" button
mode = autonomy_for(profile, sensitive=tool.is_sensitive)
if mode == "confirm_first":
    show_confirmation_step(...)
elif mode == "manual":
    show_dry_run_only(...)
elif mode == "automatic":
    execute_directly(...)
```

You can use both patterns together — render the prompt from `rendered_prompt`
but read `autonomy` for tool-execution gating.

---

## When to refresh

The profile lives in the `/me` response. Refresh whenever:

| Event | Action |
|---|---|
| Initial page load | Call `/me` once, cache `user.profile` for the session |
| User updates preferences via your UI | The PUT response already contains the fresh profile — replace your cache from it |
| Long session (> 1 hour) | Re-call `/me` opportunistically when the access token refreshes |
| `version` field changes | Drop cache, treat as the user updated something |

Don't poll `/me` per turn — the profile rarely changes. Once per session
boot is enough.

---

## First-run UX (un-onboarded users)

```python
user = me_response["user"]
if not user["is_onboarded"]:
    # User just signed up. Profile is null.
    # Either prompt them to onboard, or just use the base system prompt
    # for now — your call.
    show_onboarding_nudge()
```

Once they complete onboarding, `is_onboarded == True` and `profile` is
populated.

---

## Caching tips

- **Per-session**: cache in your request context. One `/me` call per session.
- **Per-instance**: if your service is high-throughput, cache by `user_id` with a 60s TTL. Match the JWT lifetime so a logged-out user doesn't keep their old profile.
- **Across instances**: don't bother. The profile is small (~1 KB) and `/me` is fast (single Postgres SELECT).

Watch for the `version` field — if it changes between your cached copy
and a fresh fetch, the template was upgraded server-side and you should
discard the cache.

---

## Concrete example — chat injection

```python
# app/pipeline/build_messages.py
from typing import Optional

BASE_SYSTEM = """\
You are Mobius, an AI assistant for behavioral health operations.
...
"""

def build_messages(
    base_system: str,
    profile: Optional[dict],
    history: list[dict],
    user_message: str,
) -> list[dict]:
    system_blocks = [base_system]
    if profile and profile.get("rendered_prompt"):
        system_blocks.append(profile["rendered_prompt"])
    return [
        {"role": "system", "content": "\n\n".join(system_blocks)},
        *history,
        {"role": "user", "content": user_message},
    ]
```

That's the entire integration. The `profile` is fetched once at session
start (from `/me` or from the login response) and threaded through.

---

## Don'ts

- **Don't** re-render the prompt yourself from `tasks` + `tone` + etc. — that defeats the point of the centralized template. Use `rendered_prompt`.
- **Don't** cache `profile` in localStorage/cookies — it changes when prefs change. Cache it in memory per session.
- **Don't** put `rendered_prompt` in user-facing UI verbatim — it's third-person addressable to the LLM, not first-person to the user.
- **Don't** call `/me` on every turn. Once per session boot + once per preference update is enough.

---

## Reference fields (for Pattern B users)

```ts
type UserProfile = {
  preferred_name: string | null;
  tasks: Array<{code: string; label: string}>;
  communication: {
    tone: "professional" | "friendly" | "concise";
    ai_experience_level: "beginner" | "regular" | "expert";
    greeting_enabled: boolean;
  };
  autonomy: {
    routine_tasks: "automatic" | "confirm_first" | "manual";
    sensitive_tasks: "automatic" | "confirm_first" | "manual";
  };
  timezone: string;
  rendered_prompt: string;
  version: number;
  generated_at: string;  // ISO-8601
};
```
