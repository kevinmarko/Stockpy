/**
 * Honesty badge for panels backed by synthetic/placeholder data (no real
 * data source wired yet -- see api/data_api.py and execution/options_analytics.py
 * docstrings for why). Never omit this when is_synthetic is true (CONSTRAINT #4
 * -- a demo fixture must never be presented indistinguishably from a real
 * measurement).
 */
export default function DemoDataBadge() {
  return (
    <span
      title="Synthetic placeholder data — not a live measurement"
      className="text-[10px] font-semibold uppercase tracking-wider bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 px-2 py-0.5 rounded-full border border-amber-200 dark:border-amber-800/40"
    >
      Demo Data
    </span>
  );
}
