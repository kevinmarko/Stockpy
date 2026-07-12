/** Shared, locale-aware formatting helpers. */

export function fmtUsd(v: number | null | undefined, opts?: { compact?: boolean }): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (opts?.compact && Math.abs(v) >= 1000) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(v);
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(v);
}

export function fmtPct(
  v: number | null | undefined,
  digits = 1,
  { fromFraction = false, signed = false } = {}
): string {
  if (v == null || Number.isNaN(v)) return "—";
  const val = fromFraction ? v * 100 : v;
  const s = `${val.toFixed(digits)}%`;
  return signed && val > 0 ? `+${s}` : s;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

export function fmtSignedUsd(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const s = fmtUsd(Math.abs(v));
  return v < 0 ? `-${s}` : `+${s}`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}
