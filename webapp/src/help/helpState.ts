/**
 * Per-tab "How this works" seen-state, persisted to localStorage.
 * Mirrors the `onboarding.ts` storage pattern (never throws on quota / parse).
 *
 * A tab's guide panel is expanded on first visit (educational), then collapsed
 * on every later visit once it's been marked seen. Only "seen" is persisted; the
 * expanded/collapsed toggle within a session is component-local.
 */

const KEY = "stockpy.help.seen.v1";

type SeenMap = Record<string, boolean>;

function read(): SeenMap {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as SeenMap) : {};
  } catch {
    return {};
  }
}

function write(map: SeenMap): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    /* ignore quota */
  }
}

/** Has the operator already seen this tab's guide? (→ start collapsed.) */
export function helpSeen(tabKey: string): boolean {
  return read()[tabKey] === true;
}

/** Record that the guide for this tab has been shown. */
export function markHelpSeen(tabKey: string): void {
  const map = read();
  if (map[tabKey]) return;
  map[tabKey] = true;
  write(map);
}

/** Clear all seen-state (e.g. an operator "show me the guides again" reset). */
export function resetHelpSeen(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
