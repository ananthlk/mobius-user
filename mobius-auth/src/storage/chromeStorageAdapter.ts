/** chrome.storage adapter for extension - proxies via background script */

import type { IStorageAdapter } from "./IStorageAdapter";

/** Keys that persist in chrome.storage.local (survive browser restart) */
export const PERSISTENT_KEYS = ["mobius.auth.refreshToken", "mobius.auth.userProfile"];

export const STORAGE_KEYS = {
  accessToken: "mobius.auth.accessToken",
  refreshToken: "mobius.auth.refreshToken",
  expiresAt: "mobius.auth.expiresAt",
  userProfile: "mobius.auth.userProfile",
};

/** Adapter that uses chrome.runtime.sendMessage to get/set storage via background script */
export function createChromeStorageAdapter(): IStorageAdapter {
  return {
    async get(keys: string[]) {
      return new Promise((resolve) => {
        const cb = (response: unknown) => {
          const r = response as { ok?: boolean; data?: Record<string, unknown> };
          resolve(r?.ok ? r.data ?? {} : {});
        };
        try {
          if (typeof chrome !== "undefined" && chrome?.runtime) {
            chrome.runtime.sendMessage({ type: "mobius:auth:getStorage", keys }, cb);
          } else {
            resolve({});
          }
        } catch {
          resolve({});
        }
      });
    },
    async set(items: Record<string, unknown>) {
      return new Promise((resolve) => {
        try {
          if (typeof chrome !== "undefined" && chrome?.runtime) {
            chrome.runtime.sendMessage({ type: "mobius:auth:setStorage", items }, () => resolve());
          } else {
            resolve();
          }
        } catch {
          resolve();
        }
      });
    },
    async remove(keys: string[]) {
      return new Promise((resolve) => {
        try {
          if (typeof chrome !== "undefined" && chrome?.runtime) {
            chrome.runtime.sendMessage({ type: "mobius:auth:clearStorage", keys }, () => resolve());
          } else {
            resolve();
          }
        } catch {
          resolve();
        }
      });
    },
  };
}
