/** Exported for direct unit testing (see marketSession.test.ts) -- easier to
 *  drive with fixed Date instances than mocking the system clock/timezone
 *  through the whole rendered component. */
export function computeMarketSession(now: Date): "RTH (Open)" | "PRE/POST" | "CLOSED" {
  // NYSE hours are defined in Eastern Time regardless of the operator's own
  // timezone -- deriving from the browser's local hour (the original bug
  // here) reads as "market open" for a non-ET operator at the wrong moment.
  // This is a real, deterministic function of wall-clock time (not fabricated
  // data), but it IS just a schedule approximation -- it doesn't know about
  // early closes or exchange holidays.
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "numeric",
    hour12: false,
    weekday: "short",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const weekday = get("weekday");
  const hour = Number(get("hour"));
  const minute = Number(get("minute"));
  const minutesSinceMidnight = hour * 60 + minute;

  if (weekday === "Sat" || weekday === "Sun") return "CLOSED";
  if (minutesSinceMidnight >= 9 * 60 + 30 && minutesSinceMidnight < 16 * 60) return "RTH (Open)";
  if (minutesSinceMidnight >= 4 * 60 && minutesSinceMidnight < 20 * 60) return "PRE/POST";
  return "CLOSED";
}
