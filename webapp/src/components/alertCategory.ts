import type { AlertEntry } from "../api/types";

// Split out of ActivityFeed.tsx (a pure helper, no React) so that file only
// exports the `ActivityFeed` component -- keeps Vite's React Fast Refresh
// working there instead of invalidating on every edit.
export function getAlertCategory(entry: AlertEntry): "SYSTEM" | "EXECUTION" | "RISK" | "REGIME" {
  const t = entry.extra?.type as string | undefined;
  if (!t) return "SYSTEM";
  if (["fill", "order", "trade", "execution"].includes(t)) return "EXECUTION";
  if (["risk", "constraint"].includes(t)) return "RISK";
  if (["regime", "hmm", "macro"].includes(t)) return "REGIME";
  return "SYSTEM";
}
