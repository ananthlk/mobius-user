/**
 * @mobius/auth - Shared auth for extension and chat
 *
 * Usage (extension):
 *   import { createAuthService, createChromeStorageAdapter, createAuthModal, AUTH_STYLES } from '@mobius/auth';
 *   const storage = createChromeStorageAdapter();
 *   const auth = createAuthService({ apiBase: API_V1_URL, storage });
 *   const modal = createAuthModal({ auth, showOAuth: true });
 *
 * Usage (chat):
 *   import { createAuthService, localStorageAdapter, createAuthModal, AUTH_STYLES } from '@mobius/auth';
 *   const auth = createAuthService({ apiBase: `${origin}/api/v1`, storage: localStorageAdapter });
 *   const modal = createAuthModal({ auth });
 */

export * from "./types";
export { AuthService, type AuthServiceConfig, type AuthEvent, type AuthEventCallback } from "./AuthService";
export { createChromeStorageAdapter, STORAGE_KEYS, PERSISTENT_KEYS } from "./storage/chromeStorageAdapter";
export { localStorageAdapter } from "./storage/localStorageAdapter";
export type { IStorageAdapter } from "./storage/IStorageAdapter";
export { createAuthModal, type AuthModalOptions, type AuthModalMode } from "./AuthModal";
export { createPreferencesModal, PREFERENCES_MODAL_STYLES, type PreferencesModalOptions, type ActivityOption } from "./PreferencesModal";
export { createUserMenu, USER_MENU_STYLES, type UserMenuOptions } from "./UserMenu";
export { AUTH_STYLES } from "./styles";

import { AuthService } from "./AuthService";
import type { IStorageAdapter } from "./storage/IStorageAdapter";

export function createAuthService(config: { apiBase: string; storage: IStorageAdapter }): AuthService {
  return new AuthService(config);
}
