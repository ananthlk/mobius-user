/**
 * Shared user menu (account dropdown) - same look as extension.
 * Shows avatar, name, email, then My Preferences / Switch account / Sign out.
 */

import type { AuthService } from "./AuthService";
import type { UserProfile } from "./types";

function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function getDropdownPosition(
  anchorRect: DOMRect,
  dropdownWidth: number,
  dropdownHeight: number,
  options: { preferAbove?: boolean; gap?: number } = {}
): { top: number; left: number; transformOrigin: string } {
  const { preferAbove = false, gap = 8 } = options;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let top: number;
  let left: number;
  let transformOrigin = "top left";

  const spaceAbove = anchorRect.top;
  const spaceBelow = vh - anchorRect.bottom;
  if (preferAbove && spaceAbove >= dropdownHeight + gap) {
    top = anchorRect.top - dropdownHeight - gap;
    transformOrigin = "bottom left";
  } else if (spaceBelow >= dropdownHeight + gap) {
    top = anchorRect.bottom + gap;
    transformOrigin = "top left";
  } else if (spaceAbove > spaceBelow) {
    top = Math.max(gap, anchorRect.top - dropdownHeight - gap);
    transformOrigin = "bottom left";
  } else {
    top = anchorRect.bottom + gap;
    transformOrigin = "top left";
  }

  const spaceRight = vw - anchorRect.left;
  const spaceLeft = anchorRect.right;
  if (spaceRight >= dropdownWidth) {
    left = anchorRect.left;
  } else if (spaceLeft >= dropdownWidth) {
    left = anchorRect.right - dropdownWidth;
    transformOrigin = transformOrigin.replace("left", "right");
  } else {
    left = Math.max(gap, Math.min(anchorRect.left, vw - dropdownWidth - gap));
  }
  return { top, left, transformOrigin };
}

export interface UserMenuOptions {
  auth: AuthService;
  /** Called when user clicks "My Preferences" */
  onOpenPreferences?: () => void;
  /** Called after sign out */
  onSignOut?: () => void;
  /** Called when user clicks "Not you? Sign in differently" (optional; default is same as sign out) */
  onSwitchAccount?: () => void;
}

export function createUserMenu(options: UserMenuOptions): {
  show: (anchor: HTMLElement) => Promise<void>;
  hide: () => void;
} {
  const { auth, onOpenPreferences, onSignOut, onSwitchAccount } = options;
  let menuEl: HTMLElement | null = null;
  let closeListener: ((e: MouseEvent) => void) | null = null;
  let stylesInjected = false;
  function ensureStyles(): void {
    if (stylesInjected || document.getElementById("mobius-user-menu-styles")) {
      stylesInjected = true;
      return;
    }
    const style = document.createElement("style");
    style.id = "mobius-user-menu-styles";
    style.textContent = USER_MENU_STYLES;
    document.head.appendChild(style);
    stylesInjected = true;
  }

  function hide(): void {
    if (closeListener) {
      document.removeEventListener("click", closeListener);
      closeListener = null;
    }
    if (menuEl?.parentNode) {
      menuEl.parentNode.removeChild(menuEl);
      menuEl = null;
    }
  }

  async function show(anchor: HTMLElement): Promise<void> {
    hide();
    ensureStyles();
    const user = await auth.getUserProfile();
    if (!user) return;

    const displayName =
      user.preferred_name || user.first_name || user.display_name || user.email || "User";
    const email = user.email || "";
    const initial = (displayName || "?")[0].toUpperCase();

    const dropdownWidth = Math.max(anchor.getBoundingClientRect().width, 220);
    const dropdownHeight = 200;
    const rect = anchor.getBoundingClientRect();
    const pos = getDropdownPosition(rect, dropdownWidth, dropdownHeight, {
      preferAbove: false,
      gap: 4,
    });

    menuEl = document.createElement("div");
    menuEl.className = "mobius-user-menu";
    menuEl.setAttribute("role", "menu");
    menuEl.style.cssText = `
      position: fixed;
      top: ${pos.top}px;
      left: ${pos.left}px;
      width: ${dropdownWidth}px;
      background: white;
      border-radius: 8px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
      z-index: 10001;
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      transform-origin: ${pos.transformOrigin};
    `;

    menuEl.innerHTML = `
      <div class="mobius-user-menu-header">
        <div class="mobius-user-menu-avatar">${escapeHtml(initial)}</div>
        <div class="mobius-user-menu-info">
          <div class="mobius-user-menu-name">${escapeHtml(displayName)}</div>
          ${email ? `<div class="mobius-user-menu-email">${escapeHtml(email)}</div>` : ""}
        </div>
      </div>
      <div class="mobius-user-menu-divider"></div>
      <button type="button" class="mobius-user-menu-item" data-action="preferences">
        <svg viewBox="0 0 24 24" width="14" height="14" class="mobius-user-menu-icon"><path fill="currentColor" d="M19.14 12.94c.04-.31.06-.63.06-.94 0-.31-.02-.63-.06-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.04.31-.06.63-.06.94s.02.63.06.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
        <span>My Preferences</span>
      </button>
      <button type="button" class="mobius-user-menu-item" data-action="switch">
        <svg viewBox="0 0 24 24" width="14" height="14" class="mobius-user-menu-icon"><path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>
        <span>Not you? Sign in differently</span>
      </button>
      <div class="mobius-user-menu-divider"></div>
      <button type="button" class="mobius-user-menu-item mobius-user-menu-item--danger" data-action="signout">
        <svg viewBox="0 0 24 24" width="14" height="14" class="mobius-user-menu-icon"><path fill="currentColor" d="M17 7l-1.41 1.41L18.17 11H8v2h10.17l-2.58 2.58L17 17l5-5zM4 5h8V3H4c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8v-2H4V5z"/></svg>
        <span>Sign out</span>
      </button>
    `;

    menuEl.querySelectorAll(".mobius-user-menu-item").forEach((btn) => {
      btn.addEventListener("mouseenter", () => {
        (btn as HTMLElement).style.background = "#f8fafc";
      });
      btn.addEventListener("mouseleave", () => {
        (btn as HTMLElement).style.background = "transparent";
      });
      btn.addEventListener("click", async (e) => {
        e.preventDefault();
        const action = (btn as HTMLElement).dataset.action;
        hide();
        if (action === "preferences") {
          onOpenPreferences?.();
        } else if (action === "signout") {
          await auth.logout();
          onSignOut?.();
        } else if (action === "switch") {
          await auth.logout();
          (onSwitchAccount ?? onSignOut)?.();
        }
      });
    });

    document.body.appendChild(menuEl);

    const listener = (e: MouseEvent) => {
      if (
        menuEl &&
        !menuEl.contains(e.target as Node) &&
        !anchor.contains(e.target as Node)
      ) {
        hide();
      }
    };
    closeListener = listener;
    setTimeout(() => document.addEventListener("click", listener), 0);
  }

  return { show, hide };
}

export const USER_MENU_STYLES = `
.mobius-user-menu-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  background: #f8fafc;
}
.mobius-user-menu-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #3b82f6;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 14px;
  flex-shrink: 0;
}
.mobius-user-menu-info {
  flex: 1;
  min-width: 0;
}
.mobius-user-menu-name {
  font-size: 11px;
  font-weight: 600;
  color: #0b1220;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.mobius-user-menu-email {
  font-size: 9px;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.mobius-user-menu-divider {
  height: 1px;
  background: #e2e8f0;
}
.mobius-user-menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 10px;
  color: #374151;
  text-align: left;
  font-family: inherit;
}
.mobius-user-menu-item:hover {
  background: #f8fafc;
}
.mobius-user-menu-icon {
  color: #64748b;
  flex-shrink: 0;
}
.mobius-user-menu-item--danger .mobius-user-menu-icon,
.mobius-user-menu-item--danger {
  color: #dc2626;
}
`;
