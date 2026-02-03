/** localStorage adapter for web apps (mobius-chat) */

import type { IStorageAdapter } from "./IStorageAdapter";

export const STORAGE_KEYS = {
  accessToken: "mobius.auth.accessToken",
  refreshToken: "mobius.auth.refreshToken",
  expiresAt: "mobius.auth.expiresAt",
  userProfile: "mobius.auth.userProfile",
};

export const localStorageAdapter: IStorageAdapter = {
  async get(keys: string[]) {
    const out: Record<string, unknown> = {};
    for (const k of keys) {
      try {
        const v = localStorage.getItem(k);
        if (v != null) {
          if (k === STORAGE_KEYS.expiresAt) out[k] = Number(v);
          else if (k === STORAGE_KEYS.userProfile) out[k] = JSON.parse(v);
          else out[k] = v;
        }
      } catch {
        // ignore
      }
    }
    return out;
  },
  async set(items: Record<string, unknown>) {
    for (const [k, v] of Object.entries(items)) {
      try {
        if (v == null) localStorage.removeItem(k);
        else if (typeof v === "object") localStorage.setItem(k, JSON.stringify(v));
        else localStorage.setItem(k, String(v));
      } catch {
        // ignore
      }
    }
  },
  async remove(keys: string[]) {
    for (const k of keys) localStorage.removeItem(k);
  },
};
