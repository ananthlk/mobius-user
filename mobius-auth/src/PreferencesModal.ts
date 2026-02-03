/**
 * Shared Preferences Modal - user preferences (profile, activities, AI comfort, display).
 * Same UI for extension and chat. Uses apiBase and AuthService for API calls.
 */

import type { AuthService } from "./AuthService";
import type { UserPreferences } from "./types";

export interface PreferencesModalOptions {
  /** Callback when preferences are saved */
  onSave?: (preferences: UserPreferences) => void;
  /** Callback when modal is closed */
  onClose?: () => void;
}

export interface ActivityOption {
  activity_code: string;
  label: string;
  description?: string;
}

function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function prefsFromProfile(profile: {
  preferred_name?: string;
  timezone?: string;
  activities?: string[];
  tone?: "professional" | "friendly" | "concise";
  greeting_enabled?: boolean;
  autonomy_routine_tasks?: "automatic" | "confirm_first" | "manual";
  autonomy_sensitive_tasks?: "automatic" | "confirm_first" | "manual";
}): UserPreferences {
  return {
    preferred_name: profile.preferred_name ?? "",
    timezone: profile.timezone ?? "America/New_York",
    activities: profile.activities ?? [],
    tone: profile.tone ?? "professional",
    greeting_enabled: profile.greeting_enabled !== false,
    autonomy_routine_tasks: profile.autonomy_routine_tasks ?? "confirm_first",
    autonomy_sensitive_tasks: profile.autonomy_sensitive_tasks ?? "manual",
  };
}

export function createPreferencesModal(
  apiBase: string,
  auth: AuthService,
  options?: PreferencesModalOptions
): { open: () => Promise<void>; close: () => void } {
  const base = apiBase.replace(/\/$/, "");
  const authBase = `${base}/auth`;
  let modalEl: HTMLElement | null = null;
  let stylesInjected = false;

  function ensureStyles(): void {
    if (stylesInjected || document.getElementById("mobius-prefs-styles")) {
      stylesInjected = true;
      return;
    }
    const style = document.createElement("style");
    style.id = "mobius-prefs-styles";
    style.textContent = PREFERENCES_MODAL_STYLES;
    document.head.appendChild(style);
    stylesInjected = true;
  }

  function close(): void {
    if (modalEl && modalEl.parentNode) {
      modalEl.parentNode.removeChild(modalEl);
      modalEl = null;
    }
    options?.onClose?.();
  }

  async function open(): Promise<void> {
    const token = await auth.getAccessToken();
    if (!token) {
      console.warn("[PreferencesModal] Not signed in");
      return;
    }

    ensureStyles();

    let activities: ActivityOption[] = [];
    try {
      const res = await fetch(`${authBase}/activities`);
      const data = await res.json();
      if (data.ok && Array.isArray(data.activities)) {
        activities = data.activities.map((a: { activity_code: string; label: string; description?: string }) => ({
          activity_code: a.activity_code,
          label: a.label,
          description: a.description,
        }));
      }
    } catch (e) {
      console.error("[PreferencesModal] Error fetching activities:", e);
    }

    const profile = await auth.getCurrentUser();
    const initialPrefs = profile ? prefsFromProfile(profile) : prefsFromProfile({});
    const prefs: UserPreferences = { ...initialPrefs, activities: [...(initialPrefs.activities ?? [])] };
    const selectedActivities = [...(prefs.activities ?? [])];

    const modal = document.createElement("div");
    modal.className = "mobius-prefs-modal";
    let activeTab = "profile";

    function render(): void {
      modal.innerHTML = `
      <div class="mobius-prefs-backdrop"></div>
      <div class="mobius-prefs-container">
        <div class="mobius-prefs-header">
          <h2>My Preferences</h2>
          <button class="mobius-prefs-close" type="button">
            <svg viewBox="0 0 24 24" width="18" height="18">
              <path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
            </svg>
          </button>
        </div>
        <div class="mobius-prefs-tabs">
          <button class="mobius-prefs-tab ${activeTab === "profile" ? "active" : ""}" data-tab="profile">Profile</button>
          <button class="mobius-prefs-tab ${activeTab === "activities" ? "active" : ""}" data-tab="activities">Activities</button>
          <button class="mobius-prefs-tab ${activeTab === "ai" ? "active" : ""}" data-tab="ai">AI Comfort</button>
          <button class="mobius-prefs-tab ${activeTab === "display" ? "active" : ""}" data-tab="display">Display</button>
        </div>
        <div class="mobius-prefs-content">
          ${renderTabContent()}
        </div>
        <div class="mobius-prefs-footer">
          <button class="mobius-prefs-btn-cancel" type="button">Cancel</button>
          <button class="mobius-prefs-btn-save" type="button">Save Changes</button>
        </div>
      </div>
    `;
      wireEvents();
    }

    function renderTabContent(): string {
      const tone = prefs.tone ?? "professional";
      const routine = prefs.autonomy_routine_tasks ?? "confirm_first";
      const sensitive = prefs.autonomy_sensitive_tasks ?? "manual";
      const greeting = prefs.greeting_enabled !== false;
      switch (activeTab) {
        case "profile":
          return `
          <div class="mobius-prefs-section">
            <label class="mobius-prefs-label">Preferred Name</label>
            <input type="text" class="mobius-prefs-input" id="pref-name"
                   value="${escapeHtml(prefs.preferred_name ?? "")}"
                   placeholder="How should we greet you?" />
          </div>
          <div class="mobius-prefs-section">
            <label class="mobius-prefs-label">Timezone</label>
            <select class="mobius-prefs-select" id="pref-timezone">
              <option value="America/New_York" ${prefs.timezone === "America/New_York" ? "selected" : ""}>Eastern Time (ET)</option>
              <option value="America/Chicago" ${prefs.timezone === "America/Chicago" ? "selected" : ""}>Central Time (CT)</option>
              <option value="America/Denver" ${prefs.timezone === "America/Denver" ? "selected" : ""}>Mountain Time (MT)</option>
              <option value="America/Los_Angeles" ${prefs.timezone === "America/Los_Angeles" ? "selected" : ""}>Pacific Time (PT)</option>
            </select>
          </div>
        `;
        case "activities":
          return `
          <p class="mobius-prefs-desc">Select the activities you work on. This helps Mobius show you relevant quick actions and tasks.</p>
          <div class="mobius-prefs-activities">
            ${activities.map((a) => `
              <label class="mobius-prefs-activity ${selectedActivities.includes(a.activity_code) ? "selected" : ""}">
                <input type="checkbox" value="${escapeHtml(a.activity_code)}" ${selectedActivities.includes(a.activity_code) ? "checked" : ""} />
                <span class="mobius-prefs-activity-check">
                  <svg viewBox="0 0 24 24" width="14" height="14">
                    <path fill="currentColor" d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                  </svg>
                </span>
                <span>${escapeHtml(a.label)}</span>
              </label>
            `).join("")}
          </div>
        `;
        case "ai":
          return `
          <div class="mobius-prefs-section">
            <label class="mobius-prefs-label">For routine tasks (eligibility checks, status updates):</label>
            <div class="mobius-prefs-options">
              <label class="mobius-prefs-option ${routine === "automatic" ? "selected" : ""}">
                <input type="radio" name="routine" value="automatic" ${routine === "automatic" ? "checked" : ""} />
                <span>Do it automatically</span>
              </label>
              <label class="mobius-prefs-option ${routine === "confirm_first" ? "selected" : ""}">
                <input type="radio" name="routine" value="confirm_first" ${routine === "confirm_first" ? "checked" : ""} />
                <span>Show me first, then confirm</span>
              </label>
              <label class="mobius-prefs-option ${routine === "manual" ? "selected" : ""}">
                <input type="radio" name="routine" value="manual" ${routine === "manual" ? "checked" : ""} />
                <span>Just guide me, I'll do it</span>
              </label>
            </div>
          </div>
          <div class="mobius-prefs-section">
            <label class="mobius-prefs-label">For sensitive tasks (billing, patient records):</label>
            <div class="mobius-prefs-options">
              <label class="mobius-prefs-option ${sensitive === "automatic" ? "selected" : ""}">
                <input type="radio" name="sensitive" value="automatic" ${sensitive === "automatic" ? "checked" : ""} />
                <span>Do it automatically</span>
              </label>
              <label class="mobius-prefs-option ${sensitive === "confirm_first" ? "selected" : ""}">
                <input type="radio" name="sensitive" value="confirm_first" ${sensitive === "confirm_first" ? "checked" : ""} />
                <span>Always show me before acting</span>
              </label>
              <label class="mobius-prefs-option ${sensitive === "manual" ? "selected" : ""}">
                <input type="radio" name="sensitive" value="manual" ${sensitive === "manual" ? "checked" : ""} />
                <span>Never act without my approval</span>
              </label>
            </div>
          </div>
        `;
        case "display":
          return `
          <div class="mobius-prefs-section">
            <label class="mobius-prefs-label">Communication Tone</label>
            <div class="mobius-prefs-options">
              <label class="mobius-prefs-option ${tone === "professional" ? "selected" : ""}">
                <input type="radio" name="tone" value="professional" ${tone === "professional" ? "checked" : ""} />
                <span>Professional</span>
              </label>
              <label class="mobius-prefs-option ${tone === "friendly" ? "selected" : ""}">
                <input type="radio" name="tone" value="friendly" ${tone === "friendly" ? "checked" : ""} />
                <span>Friendly</span>
              </label>
              <label class="mobius-prefs-option ${tone === "concise" ? "selected" : ""}">
                <input type="radio" name="tone" value="concise" ${tone === "concise" ? "checked" : ""} />
                <span>Concise</span>
              </label>
            </div>
          </div>
          <div class="mobius-prefs-section">
            <label class="mobius-prefs-toggle">
              <input type="checkbox" id="pref-greeting" ${greeting ? "checked" : ""} />
              <span class="mobius-prefs-toggle-slider"></span>
              <span class="mobius-prefs-toggle-label">Show personalized greeting</span>
            </label>
          </div>
        `;
        default:
          return "";
      }
    }

    function updateRadioStyles(name: string): void {
      modal.querySelectorAll(`input[name="${name}"]`).forEach((r) => {
        const label = (r as HTMLInputElement).closest(".mobius-prefs-option");
        if ((r as HTMLInputElement).checked) label?.classList.add("selected");
        else label?.classList.remove("selected");
      });
    }

    function wireEvents(): void {
      modal.querySelector(".mobius-prefs-close")?.addEventListener("click", close);
      modal.querySelector(".mobius-prefs-backdrop")?.addEventListener("click", close);
      modal.querySelector(".mobius-prefs-btn-cancel")?.addEventListener("click", close);

      modal.querySelector(".mobius-prefs-btn-save")?.addEventListener("click", async () => {
        const saveBtn = modal.querySelector<HTMLButtonElement>(".mobius-prefs-btn-save");
        if (saveBtn) {
          saveBtn.textContent = "Saving...";
          saveBtn.disabled = true;
        }
        try {
          const t = await auth.getAccessToken();
          if (!t) {
            close();
            return;
          }
          const response = await fetch(`${authBase}/preferences`, {
            method: "PUT",
            headers: {
              Authorization: `Bearer ${t}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              preferred_name: prefs.preferred_name,
              timezone: prefs.timezone,
              activities: selectedActivities,
              tone: prefs.tone,
              greeting_enabled: prefs.greeting_enabled,
              autonomy_routine_tasks: prefs.autonomy_routine_tasks,
              autonomy_sensitive_tasks: prefs.autonomy_sensitive_tasks,
            }),
          });
          if (response.ok) {
            prefs.activities = [...selectedActivities];
            await auth.getCurrentUser();
            options?.onSave?.(prefs);
            close();
          } else {
            const errData = await response.json().catch(() => ({}));
            console.error("[PreferencesModal] Error saving preferences:", errData);
            alert("Failed to save preferences. Please try again.");
          }
        } catch (error) {
          console.error("[PreferencesModal] Error saving preferences:", error);
          alert("Failed to save preferences. Please try again.");
        } finally {
          const btn = modal.querySelector<HTMLButtonElement>(".mobius-prefs-btn-save");
          if (btn) {
            btn.textContent = "Save Changes";
            btn.disabled = false;
          }
        }
      });

      modal.querySelectorAll(".mobius-prefs-tab").forEach((tab) => {
        tab.addEventListener("click", () => {
          activeTab = (tab as HTMLElement).dataset.tab ?? "profile";
          render();
        });
      });

      modal.querySelector("#pref-name")?.addEventListener("input", (e) => {
        prefs.preferred_name = (e.target as HTMLInputElement).value;
      });
      modal.querySelector("#pref-timezone")?.addEventListener("change", (e) => {
        prefs.timezone = (e.target as HTMLSelectElement).value;
      });

      modal.querySelectorAll(".mobius-prefs-activity input").forEach((cb) => {
        cb.addEventListener("change", (e) => {
          const code = (e.target as HTMLInputElement).value;
          const checked = (e.target as HTMLInputElement).checked;
          const label = (e.target as HTMLInputElement).closest(".mobius-prefs-activity");
          if (checked) {
            if (!selectedActivities.includes(code)) selectedActivities.push(code);
            label?.classList.add("selected");
          } else {
            const idx = selectedActivities.indexOf(code);
            if (idx > -1) selectedActivities.splice(idx, 1);
            label?.classList.remove("selected");
          }
        });
      });

      ["routine", "sensitive", "tone"].forEach((name) => {
        modal.querySelectorAll(`input[name="${name}"]`).forEach((r) => {
          r.addEventListener("change", (e) => {
            const value = (e.target as HTMLInputElement).value;
            if (name === "routine") prefs.autonomy_routine_tasks = value as UserPreferences["autonomy_routine_tasks"];
            if (name === "sensitive") prefs.autonomy_sensitive_tasks = value as UserPreferences["autonomy_sensitive_tasks"];
            if (name === "tone") prefs.tone = value as UserPreferences["tone"];
            updateRadioStyles(name);
          });
        });
      });

      modal.querySelector("#pref-greeting")?.addEventListener("change", (e) => {
        prefs.greeting_enabled = (e.target as HTMLInputElement).checked;
      });
    }

    render();
    modalEl = modal;
    document.body.appendChild(modal);
  }

  return { open, close };
}

export const PREFERENCES_MODAL_STYLES = `
.mobius-prefs-modal {
  position: fixed;
  inset: 0;
  z-index: 10000;
}
.mobius-prefs-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
}
.mobius-prefs-container {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: white;
  border-radius: 12px;
  width: 90%;
  max-width: 420px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
}
.mobius-prefs-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #e2e8f0;
}
.mobius-prefs-header h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: #0b1220;
}
.mobius-prefs-close {
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px;
  color: #64748b;
}
.mobius-prefs-close:hover {
  color: #374151;
}
.mobius-prefs-tabs {
  display: flex;
  border-bottom: 1px solid #e2e8f0;
  padding: 0 12px;
}
.mobius-prefs-tab {
  padding: 10px 12px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  font-size: 11px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.15s;
}
.mobius-prefs-tab:hover {
  color: #374151;
}
.mobius-prefs-tab.active {
  color: #3b82f6;
  border-bottom-color: #3b82f6;
}
.mobius-prefs-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}
.mobius-prefs-section {
  margin-bottom: 16px;
}
.mobius-prefs-section:last-child {
  margin-bottom: 0;
}
.mobius-prefs-label {
  display: block;
  font-size: 11px;
  font-weight: 500;
  color: #374151;
  margin-bottom: 8px;
}
.mobius-prefs-desc {
  font-size: 10px;
  color: #64748b;
  margin: 0 0 12px;
}
.mobius-prefs-input,
.mobius-prefs-select {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 12px;
  box-sizing: border-box;
}
.mobius-prefs-input:focus,
.mobius-prefs-select:focus {
  outline: none;
  border-color: #3b82f6;
}
.mobius-prefs-activities {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.mobius-prefs-activity {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  cursor: pointer;
  font-size: 10px;
  color: #374151;
  transition: all 0.15s;
}
.mobius-prefs-activity:hover {
  background: #f1f5f9;
}
.mobius-prefs-activity.selected {
  background: #eff6ff;
  border-color: #3b82f6;
}
.mobius-prefs-activity input {
  display: none;
}
.mobius-prefs-activity-check {
  width: 14px;
  height: 14px;
  border: 1px solid #cbd5e1;
  border-radius: 3px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
}
.mobius-prefs-activity.selected .mobius-prefs-activity-check {
  background: #3b82f6;
  border-color: #3b82f6;
}
.mobius-prefs-options {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.mobius-prefs-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  cursor: pointer;
  font-size: 11px;
  color: #374151;
  transition: all 0.15s;
}
.mobius-prefs-option:hover {
  background: #f1f5f9;
}
.mobius-prefs-option.selected {
  background: #eff6ff;
  border-color: #3b82f6;
}
.mobius-prefs-option input {
  display: none;
}
.mobius-prefs-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}
.mobius-prefs-toggle input {
  display: none;
}
.mobius-prefs-toggle-slider {
  width: 36px;
  height: 20px;
  background: #e2e8f0;
  border-radius: 10px;
  position: relative;
  transition: background 0.2s;
}
.mobius-prefs-toggle-slider::after {
  content: '';
  position: absolute;
  width: 16px;
  height: 16px;
  background: white;
  border-radius: 50%;
  top: 2px;
  left: 2px;
  transition: transform 0.2s;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
}
.mobius-prefs-toggle input:checked + .mobius-prefs-toggle-slider {
  background: #3b82f6;
}
.mobius-prefs-toggle input:checked + .mobius-prefs-toggle-slider::after {
  transform: translateX(16px);
}
.mobius-prefs-toggle-label {
  font-size: 11px;
  color: #374151;
}
.mobius-prefs-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 20px;
  border-top: 1px solid #e2e8f0;
}
.mobius-prefs-btn-cancel {
  padding: 8px 16px;
  background: none;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 11px;
  color: #64748b;
  cursor: pointer;
}
.mobius-prefs-btn-cancel:hover {
  background: #f8fafc;
}
.mobius-prefs-btn-save {
  padding: 8px 16px;
  background: #3b82f6;
  border: none;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 500;
  color: white;
  cursor: pointer;
}
.mobius-prefs-btn-save:hover {
  background: #2563eb;
}
`;
