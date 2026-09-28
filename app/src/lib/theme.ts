export type Theme = "dark" | "light";

export function readPreference(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writePreference(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Preferences are a convenience; failing to persist them is harmless.
  }
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  writePreference("hyverion.theme", theme);
}
