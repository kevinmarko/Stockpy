import type { PerfRange } from "../api/types";

const RANGES: PerfRange[] = ["1W", "1M", "3M", "6M", "1Y", "2Y"];

export function RangeToggle({
  value,
  onChange,
}: {
  value: PerfRange;
  onChange: (r: PerfRange) => void;
}) {
  return (
    <div className="segmented" role="tablist" aria-label="Performance range">
      {RANGES.map((r) => (
        <button
          key={r}
          role="tab"
          aria-selected={r === value}
          className={r === value ? "on" : ""}
          onClick={() => onChange(r)}
        >
          {r}
        </button>
      ))}
    </div>
  );
}
