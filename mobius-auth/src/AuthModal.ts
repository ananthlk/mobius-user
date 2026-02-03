/**
 * Shared AuthModal - login, signup, account view.
 * Same UI for extension and chat. Accepts optional OAuth/SSO slots.
 */

import type { AuthService } from "./AuthService";
import type { UserProfile } from "./types";

function escapeHtml(s: string): string {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

export interface AuthModalOptions {
  /** AuthService instance */
  auth: AuthService;
  /** Optional: show OAuth/SSO buttons (Google, Microsoft, Enterprise) */
  showOAuth?: boolean;
  /** Optional: demo email hint (e.g. sarah.chen@demo.clinic or admin for admin@demo.clinic) */
  demoEmail?: string;
  /** Callback when user signs in successfully */
  onSuccess?: (user: UserProfile) => void;
  /** Callback when modal closes */
  onClose?: () => void;
}

export type AuthModalMode = "login" | "signup" | "account";

export function createAuthModal(options: AuthModalOptions): {
  el: HTMLElement;
  open: (mode?: AuthModalMode) => void;
  close: () => void;
  updateUser: (user: UserProfile | null) => void;
} {
  const { auth, showOAuth = true, demoEmail, onSuccess, onClose } = options;
  let currentUser: UserProfile | null = null;

  const overlay = document.createElement("div");
  overlay.className = "mobius-auth-overlay";
  overlay.setAttribute("aria-hidden", "true");

  const panel = document.createElement("div");
  panel.className = "mobius-auth-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-labelledby", "mobius-auth-title");

  function close() {
    overlay.classList.remove("open");
    overlay.setAttribute("aria-hidden", "true");
    onClose?.();
  }

  function open(mode: AuthModalMode = "login") {
    currentUser = null;
    auth.getUserProfile().then((p) => {
      currentUser = p ?? null;
      const m = mode === "account" && currentUser ? "account" : mode;
      render(m);
    });
    overlay.classList.add("open");
    overlay.setAttribute("aria-hidden", "false");
  }

  function updateUser(user: UserProfile | null) {
    currentUser = user;
  }

  function render(mode: AuthModalMode) {
    const titleId = "mobius-auth-title";
    const hasOAuth = showOAuth;
    const loginHtml = `
      <button type="button" class="mobius-auth-close" aria-label="Close">&times;</button>
      <h2 id="${titleId}" class="mobius-auth-title">Sign in</h2>
      <div class="mobius-auth-form" data-mode="login">
        <input type="email" class="mobius-auth-email" placeholder="Email (or admin, scheduler for demo)" autocomplete="email" ${demoEmail ? `value="${escapeHtml(demoEmail)}"` : ""} />
        <input type="password" class="mobius-auth-password" placeholder="Password" autocomplete="current-password" />
        <button type="button" class="mobius-auth-btn mobius-auth-login-btn">Sign in</button>
        <div class="mobius-auth-error" style="display:none"></div>
        ${hasOAuth ? `
          <div class="mobius-auth-divider"><span>or continue with</span></div>
          <div class="mobius-auth-oauth">
            <button type="button" class="mobius-auth-oauth-btn" data-provider="google">Google</button>
            <button type="button" class="mobius-auth-oauth-btn" data-provider="microsoft">Microsoft</button>
            <button type="button" class="mobius-auth-sso-btn">Enterprise SSO</button>
          </div>
        ` : ""}
        <p class="mobius-auth-switch">No account? <button type="button" class="mobius-auth-switch-btn" data-to="signup">Sign up</button></p>
      </div>
    `;
    const signupHtml = `
      <button type="button" class="mobius-auth-close" aria-label="Close">&times;</button>
      <h2 id="${titleId}" class="mobius-auth-title">Create account</h2>
      <div class="mobius-auth-form" data-mode="signup">
        <input type="text" class="mobius-auth-display-name" placeholder="Display name (optional)" />
        <input type="text" class="mobius-auth-first-name" placeholder="First name (optional)" />
        <input type="email" class="mobius-auth-email" placeholder="Email" autocomplete="email" />
        <input type="password" class="mobius-auth-password" placeholder="Password (min 8 chars)" autocomplete="new-password" />
        <button type="button" class="mobius-auth-btn mobius-auth-signup-btn">Create account</button>
        <div class="mobius-auth-error" style="display:none"></div>
        <p class="mobius-auth-switch">Already have an account? <button type="button" class="mobius-auth-switch-btn" data-to="login">Sign in</button></p>
      </div>
    `;
    const accountHtml = `
      <button type="button" class="mobius-auth-close" aria-label="Close">&times;</button>
      <h2 id="${titleId}" class="mobius-auth-title">Account</h2>
      <div class="mobius-auth-form" data-mode="account">
        <p class="mobius-auth-user-info">${escapeHtml(currentUser?.greeting_name || currentUser?.email || currentUser?.display_name || "User")}</p>
        <a href="#" class="mobius-auth-prefs-link">Preferences</a>
        <button type="button" class="mobius-auth-btn mobius-auth-logout-btn">Sign out</button>
      </div>
    `;

    panel.innerHTML = mode === "login" ? loginHtml : mode === "signup" ? signupHtml : accountHtml;

    // Close button
    panel.querySelector(".mobius-auth-close")?.addEventListener("click", close);
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close();
    });

    if (mode === "login") {
      const loginBtn = panel.querySelector(".mobius-auth-login-btn");
      const emailInput = panel.querySelector(".mobius-auth-email") as HTMLInputElement;
      const passwordInput = panel.querySelector(".mobius-auth-password") as HTMLInputElement;
      const errorEl = panel.querySelector(".mobius-auth-error") as HTMLElement;
      const doLogin = async () => {
        const email = emailInput?.value?.trim();
        const password = passwordInput?.value;
        if (!email || !password) {
          if (errorEl) {
            errorEl.textContent = "Email and password required";
            errorEl.style.display = "block";
          }
          return;
        }
        if (errorEl) errorEl.style.display = "none";
        if (loginBtn) {
          (loginBtn as HTMLButtonElement).textContent = "Signing in...";
          (loginBtn as HTMLButtonElement).disabled = true;
        }
        const result = await auth.login(email, password);
        if (result.success && result.user) {
          onSuccess?.(result.user);
          close();
        } else {
          if (errorEl) {
            errorEl.textContent = result.error || "Login failed";
            errorEl.style.display = "block";
          }
        }
        if (loginBtn) {
          (loginBtn as HTMLButtonElement).textContent = "Sign in";
          (loginBtn as HTMLButtonElement).disabled = false;
        }
      };
      loginBtn?.addEventListener("click", () => void doLogin());
      passwordInput?.addEventListener("keydown", (e) => {
        if (e.key === "Enter") void doLogin();
      });
      panel.querySelectorAll(".mobius-auth-oauth-btn, .mobius-auth-sso-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          // OAuth/SSO coming soon - could emit event for host to handle
          if (typeof (window as unknown as { showToast?: (s: string) => void }).showToast === "function") {
            (window as unknown as { showToast: (s: string) => void }).showToast("Coming soon");
          }
        });
      });
    }

    if (mode === "signup") {
      const signupBtn = panel.querySelector(".mobius-auth-signup-btn");
      const emailInput = panel.querySelector(".mobius-auth-email") as HTMLInputElement;
      const passwordInput = panel.querySelector(".mobius-auth-password") as HTMLInputElement;
      const displayNameInput = panel.querySelector(".mobius-auth-display-name") as HTMLInputElement;
      const firstNameInput = panel.querySelector(".mobius-auth-first-name") as HTMLInputElement;
      const errorEl = panel.querySelector(".mobius-auth-error") as HTMLElement;
      const doSignup = async () => {
        const email = emailInput?.value?.trim();
        const password = passwordInput?.value;
        if (!email || !password) {
          if (errorEl) {
            errorEl.textContent = "Email and password required";
            errorEl.style.display = "block";
          }
          return;
        }
        if (password.length < 8) {
          if (errorEl) {
            errorEl.textContent = "Password must be at least 8 characters";
            errorEl.style.display = "block";
          }
          return;
        }
        if (errorEl) errorEl.style.display = "none";
        if (signupBtn) {
          (signupBtn as HTMLButtonElement).textContent = "Creating...";
          (signupBtn as HTMLButtonElement).disabled = true;
        }
        const result = await auth.register(
          email,
          password,
          displayNameInput?.value?.trim() || undefined,
          firstNameInput?.value?.trim() || undefined
        );
        if (result.success && result.user) {
          onSuccess?.(result.user);
          close();
        } else {
          if (errorEl) {
            errorEl.textContent = result.error || "Sign up failed";
            errorEl.style.display = "block";
          }
        }
        if (signupBtn) {
          (signupBtn as HTMLButtonElement).textContent = "Create account";
          (signupBtn as HTMLButtonElement).disabled = false;
        }
      };
      signupBtn?.addEventListener("click", () => void doSignup());
      passwordInput?.addEventListener("keydown", (e) => {
        if (e.key === "Enter") void doSignup();
      });
    }

    if (mode === "account") {
      panel.querySelector(".mobius-auth-logout-btn")?.addEventListener("click", async () => {
        await auth.logout();
        updateUser(null);
        close();
      });
      panel.querySelector(".mobius-auth-prefs-link")?.addEventListener("click", (e) => {
        e.preventDefault();
        close();
        (window as unknown as { onOpenPreferences?: () => void }).onOpenPreferences?.();
      });
    }

    panel.querySelectorAll(".mobius-auth-switch-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const to = (btn as HTMLElement).getAttribute("data-to") as AuthModalMode;
        render(to);
      });
    });
  }

  overlay.appendChild(panel);
  return { el: overlay, open, close, updateUser };
}
