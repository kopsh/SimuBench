// Per-viewer conveniences only (layout, theme, language). Storage can be unavailable or cleared,
// so every access is guarded and the app works without it.
export function load<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(`simubench:${key}`);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function save(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(`simubench:${key}`, JSON.stringify(value));
  } catch {
    /* storage unavailable: keep the in-memory value only */
  }
}
