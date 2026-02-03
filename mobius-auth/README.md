# @mobius/auth

Shared auth for Mobius extension and chat: login, signup, preferences. Same code, same UX.

## Install

From mobius-chat or mobius-os/extension:

```bash
npm install file:../mobius-auth
```

## Usage

### Web (mobius-chat)

```ts
import {
  createAuthService,
  localStorageAdapter,
  createAuthModal,
  AUTH_STYLES,
} from "@mobius/auth";

const apiBase = `${window.location.origin}/api/v1`;
const auth = createAuthService({ apiBase, storage: localStorageAdapter });
const modal = createAuthModal({ auth, showOAuth: true });

document.body.appendChild(modal.el);
document.head.insertAdjacentHTML("beforeend", `<style>${AUTH_STYLES}</style>`);

// Open on sidebar user click
sidebarUser.addEventListener("click", () => {
  auth.getUserProfile().then((user) => {
    modal.open(user ? "account" : "login");
  });
});
```

### Extension

```ts
import {
  createAuthService,
  createChromeStorageAdapter,
  createAuthModal,
  AUTH_STYLES,
} from "@mobius/auth";

const storage = createChromeStorageAdapter();
const auth = createAuthService({ apiBase: API_V1_URL, storage });
const modal = createAuthModal({ auth, showOAuth: true, demoEmail: "sarah.chen@demo.clinic" });
```

## API

- `createAuthService({ apiBase, storage })` – AuthService with pluggable storage
- `localStorageAdapter` – for web apps
- `createChromeStorageAdapter()` – for extension (proxies via background)
- `createAuthModal({ auth, showOAuth?, demoEmail?, onSuccess?, onClose? })` – login/signup/account modal
- `AUTH_STYLES` – CSS string to inject
