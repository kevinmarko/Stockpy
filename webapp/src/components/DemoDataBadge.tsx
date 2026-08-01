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
      className="badge badge-warn"
      style={{ textTransform: "uppercase", letterSpacing: "0.04em" }}
    >
      Demo Data
    </span>
  );
}
