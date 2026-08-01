import { useDensity } from "./DensityContext";

export function DensityToggle() {
  const { density, toggleDensity } = useDensity();

  return (
    <button
      onClick={toggleDensity}
      style={{
        background: "var(--surface-2)",
        color: "var(--text-primary)",
        border: "1px solid var(--border)",
        borderRadius: "var(--r-xs)",
        padding: "var(--s-1-5) var(--s-3)",
        fontSize: "var(--t-caption)",
        fontWeight: 600,
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        gap: "var(--s-1-5)",
      }}
    >
      <span>{density === "compact" ? "⚡ Compact (Trader)" : "📖 Spacious (Analysis)"}</span>
    </button>
  );
}
