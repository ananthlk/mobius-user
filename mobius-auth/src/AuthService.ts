/**
 * Shared AuthService - same logic for extension and chat.
 * Uses pluggable storage (chrome.storage vs localStorage) and configurable API base.
 */

import type { AuthTokens, UserProfile } from "./types";
import type { IStorageAdapter } from "./storage/IStorageAdapter";
import { STORAGE_KEYS as LS_KEYS } from "./storage/localStorageAdapter";

export type AuthEvent = "login" | "logout" | "tokenRefreshed" | "profileUpdated";
export type AuthEventCallback = (event: AuthEvent, data?: unknown) => void;

function normalizeUser(data: Record<string, unknown>): UserProfile {
  const u = data as Record<string, unknown>;
  const pref = (u.preference as Record<string, unknown>) || {};
  const activities = (u.activities as Array<{ activity_code?: string }>) || [];
  return {
    user_id: String(u.user_id ?? ""),
    tenant_id: u.tenant_id != null ? String(u.tenant_id) : "",
    email: u.email != null ? String(u.email) : undefined,
    display_name: u.display_name != null ? String(u.display_name) : undefined,
    first_name: u.first_name != null ? String(u.first_name) : undefined,
    preferred_name: u.preferred_name != null ? String(u.preferred_name) : undefined,
    greeting_name: String(u.preferred_name ?? u.first_name ?? u.display_name ?? u.email ?? "User"),
    avatar_url: u.avatar_url != null ? String(u.avatar_url) : undefined,
    timezone: String(u.timezone || "America/New_York"),
    locale: String(u.locale || "en-US"),
    is_onboarded: Boolean(u.is_onboarded),
    activities: activities.map((a) => a.activity_code || "").filter(Boolean),
    tone: (pref.tone as UserProfile["tone"]) || "professional",
    greeting_enabled: pref.greeting_enabled !== false,
    autonomy_routine_tasks: (pref.autonomy_routine_tasks as UserProfile["autonomy_routine_tasks"]) || "confirm_first",
    autonomy_sensitive_tasks: (pref.autonomy_sensitive_tasks as UserProfile["autonomy_sensitive_tasks"]) || "manual",
  };
}

const STORAGE_KEYS = {
  accessToken: "mobius.auth.accessToken",
  refreshToken: "mobius.auth.refreshToken",
  expiresAt: "mobius.auth.expiresAt",
  userProfile: "mobius.auth.userProfile",
};

export interface AuthServiceConfig {
  apiBase: string;
  storage: IStorageAdapter;
}

export class AuthService {
  private apiBase: string;
  private storage: IStorageAdapter;
  private listeners = new Set<AuthEventCallback>();
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(config: AuthServiceConfig) {
    this.apiBase = config.apiBase.replace(/\/$/, "");
    this.storage = config.storage;
  }

  on(callback: AuthEventCallback): () => void {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  private emit(event: AuthEvent, data?: unknown) {
    this.listeners.forEach((cb) => {
      try {
        cb(event, data);
      } catch (e) {
        console.error("[AuthService] listener error:", e);
      }
    });
  }

  async storeTokens(tokens: AuthTokens): Promise<void> {
    const expiresAt = Date.now() + tokens.expires_in * 1000;
    await this.storage.set({
      [STORAGE_KEYS.accessToken]: tokens.access_token,
      [STORAGE_KEYS.refreshToken]: tokens.refresh_token,
      [STORAGE_KEYS.expiresAt]: expiresAt,
    });
    this.scheduleTokenRefresh(tokens.expires_in);
  }

  private scheduleTokenRefresh(expiresIn: number): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    const ms = Math.max((expiresIn - 300) * 1000, 60000);
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      void this.refreshAccessToken();
    }, ms);
  }

  async getAccessToken(): Promise<string | null> {
    const r = await this.storage.get([STORAGE_KEYS.accessToken, STORAGE_KEYS.expiresAt]);
    const token = r[STORAGE_KEYS.accessToken] as string | undefined;
    const expiresAt = r[STORAGE_KEYS.expiresAt] as number | undefined;
    if (!token) {
      const ok = await this.refreshAccessToken();
      return ok ? this.getAccessToken() : null;
    }
    if (expiresAt && Date.now() > expiresAt - 60000) {
      const ok = await this.refreshAccessToken();
      return ok ? this.getAccessToken() : null;
    }
    return token;
  }

  async getRefreshToken(): Promise<string | null> {
    const r = await this.storage.get([STORAGE_KEYS.refreshToken]);
    return (r[STORAGE_KEYS.refreshToken] as string) || null;
  }

  async refreshAccessToken(): Promise<boolean> {
    const refreshToken = await this.getRefreshToken();
    if (!refreshToken) return false;
    try {
      const res = await fetch(`${this.apiBase}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      const data = (await res.json()) as { access_token?: string; expires_in?: number };
      if (!res.ok || !data.access_token) {
        await this.clearTokens();
        return false;
      }
      const expiresAt = Date.now() + (data.expires_in || 3600) * 1000;
      await this.storage.set({
        [STORAGE_KEYS.accessToken]: data.access_token,
        [STORAGE_KEYS.expiresAt]: expiresAt,
      });
      this.scheduleTokenRefresh(data.expires_in || 3600);
      this.emit("tokenRefreshed");
      return true;
    } catch {
      return false;
    }
  }

  async clearTokens(): Promise<void> {
    await this.storage.remove([
      STORAGE_KEYS.accessToken,
      STORAGE_KEYS.refreshToken,
      STORAGE_KEYS.expiresAt,
      STORAGE_KEYS.userProfile,
    ]);
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  async storeUserProfile(profile: UserProfile): Promise<void> {
    await this.storage.set({ [STORAGE_KEYS.userProfile]: profile });
  }

  async getUserProfile(): Promise<UserProfile | null> {
    const r = await this.storage.get([STORAGE_KEYS.userProfile]);
    const p = r[STORAGE_KEYS.userProfile];
    return (p as UserProfile) || null;
  }

  /** Map demo shortcuts (admin, scheduler, etc.) to full email for convenience */
  private resolveDemoEmail(email: string): string {
    const trimmed = (email || "").trim().toLowerCase();
    if (!trimmed || trimmed.includes("@")) return trimmed;
    const shortcuts: Record<string, string> = {
      admin: "admin@demo.clinic",
      scheduler: "scheduler@demo.clinic",
      eligibility: "eligibility@demo.clinic",
      claims: "claims@demo.clinic",
      clinical: "clinical@demo.clinic",
      sarah: "sarah.chen@demo.clinic",
    };
    return shortcuts[trimmed] || trimmed;
  }

  async login(
    email: string,
    password: string,
    tenantId?: string
  ): Promise<{ success: boolean; error?: string; user?: UserProfile }> {
    const resolvedEmail = this.resolveDemoEmail(email);
    try {
      const res = await fetch(`${this.apiBase}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: resolvedEmail, password, tenant_id: tenantId }),
      });
      let data: Record<string, unknown> = {};
      try {
        data = (await res.json()) as Record<string, unknown>;
      } catch {
        /* 404/500 may return HTML or empty body */
      }
      const dataTyped = data as {
        ok?: boolean;
        error?: string;
        access_token?: string;
        refresh_token?: string;
        expires_in?: number;
        user?: Record<string, unknown>;
      };
      if (!res.ok || !dataTyped.ok) {
        const errMsg = (dataTyped as { error?: string; detail?: string }).error
          || (dataTyped as { error?: string; detail?: string }).detail
          || (res.status === 404 ? "Auth not configured. Set MOBIUS_OS_AUTH_URL or USER_DATABASE_URL in mobius-chat/.env" : "Login failed");
        return { success: false, error: errMsg };
      }
      await this.storeTokens({
        access_token: dataTyped.access_token!,
        refresh_token: dataTyped.refresh_token!,
        expires_in: dataTyped.expires_in || 3600,
      });
      if (dataTyped.user) {
        const profile = normalizeUser(dataTyped.user as Record<string, unknown>);
        await this.storeUserProfile(profile);
        this.emit("login", profile);
        return { success: true, user: profile };
      }
      this.emit("login");
      return { success: true };
    } catch (e) {
      console.error("[AuthService] login:", e);
      return { success: false, error: "Network error" };
    }
  }

  async register(
    email: string,
    password: string,
    displayName?: string,
    firstName?: string,
    tenantId?: string
  ): Promise<{ success: boolean; error?: string; user?: UserProfile }> {
    try {
      const res = await fetch(`${this.apiBase}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          display_name: displayName,
          first_name: firstName,
          tenant_id: tenantId,
        }),
      });
      const data = (await res.json()) as {
        ok?: boolean;
        error?: string;
        access_token?: string;
        refresh_token?: string;
        expires_in?: number;
        user?: Record<string, unknown>;
      };
      if (!res.ok || !data.ok) {
        return { success: false, error: data.error || "Registration failed" };
      }
      await this.storeTokens({
        access_token: data.access_token!,
        refresh_token: data.refresh_token!,
        expires_in: data.expires_in || 3600,
      });
      if (data.user) {
        const profile = normalizeUser(data.user);
        await this.storeUserProfile(profile);
        this.emit("login", profile);
        return { success: true, user: profile };
      }
      this.emit("login");
      return { success: true };
    } catch (e) {
      console.error("[AuthService] register:", e);
      return { success: false, error: "Network error" };
    }
  }

  async logout(): Promise<void> {
    const refreshToken = await this.getRefreshToken();
    if (refreshToken) {
      try {
        await fetch(`${this.apiBase}/auth/logout`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
      } catch {
        /* ignore */
      }
    }
    await this.clearTokens();
    this.emit("logout");
  }

  async isAuthenticated(): Promise<boolean> {
    const token = await this.getAccessToken();
    return !!token;
  }

  /** Fetch current user from /auth/me and update stored profile */
  async getCurrentUser(): Promise<UserProfile | null> {
    const token = await this.getAccessToken();
    if (!token) return null;
    try {
      const res = await fetch(`${this.apiBase}/auth/me`, {
        method: "GET",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      });
      if (!res.ok) return null;
      const data = (await res.json()) as { ok?: boolean; user?: Record<string, unknown> };
      if (!data.ok || !data.user) return null;
      const profile = normalizeUser(data.user);
      await this.storeUserProfile(profile);
      return profile;
    } catch {
      return null;
    }
  }

  /** Auth state: unauthenticated | authenticated | onboarding */
  async getAuthState(): Promise<"unauthenticated" | "authenticated" | "onboarding"> {
    const token = await this.getAccessToken();
    if (!token) return "unauthenticated";
    const profile = await this.getUserProfile();
    if (profile && profile.is_onboarded === false) return "onboarding";
    return "authenticated";
  }

  /** Check if email exists (for page detection) */
  async checkEmail(
    email: string,
    tenantId?: string
  ): Promise<{ exists: boolean; user?: { display_name?: string; is_onboarded?: boolean } }> {
    try {
      const res = await fetch(`${this.apiBase}/auth/check-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, tenant_id: tenantId }),
      });
      const data = (await res.json()) as { exists?: boolean; user?: unknown };
      return { exists: data.exists === true, user: data.user as { display_name?: string; is_onboarded?: boolean } };
    } catch {
      return { exists: false };
    }
  }

  /** Get Authorization header for API calls */
  async getAuthHeader(): Promise<{ Authorization: string } | null> {
    const token = await this.getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : null;
  }
}
