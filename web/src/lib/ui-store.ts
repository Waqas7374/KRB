import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

/**
 * uiStore — per-user presentation preferences (docs/08 §1). Persisted to
 * localStorage; losing it (private window, cleared storage) only resets the
 * layout, never data.
 */

export type Density = "comfortable" | "compact" | "dense";
export type Theme = "system" | "light" | "dark";

interface UiState {
  density: Density;
  sidebarCollapsed: boolean;
  theme: Theme;
  setDensity: (density: Density) => void;
  toggleSidebar: () => void;
  setTheme: (theme: Theme) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      density: "compact",
      sidebarCollapsed: false,
      theme: "system",
      setDensity: (density) => set({ density }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setTheme: (theme) => set({ theme }),
    }),
    { name: "krb.ui", storage: createJSONStorage(() => localStorage) },
  ),
);

/** Applies the theme as the `data-theme` attribute the token CSS keys off. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}
