import type { TrendsSeam } from "../api/types";
import { theme } from "../theme";

const DASH = "—";

function num(v: number | null | undefined, digits: number): string {
  return v != null && Number.isFinite(v) ? v.toFixed(digits) : DASH;
}

export function TrendsSeamsTable({ seams }: { seams: TrendsSeam[] }) {
  if (!seams || seams.length === 0) {
    return (
      <div
        className="empty"
        data-testid="trends-seams-empty"
        style={{ padding: "var(--s-6) var(--s-2)", background: "var(--surface-2)", borderRadius: "var(--r-md)" }}
      >
        No seams to show
      </div>
    );
  }
  return (
    <div style={{ overflowX: "auto" }} data-testid="trends-seams-table">
      <table className="table" style={{ minWidth: 720 }}>
        <thead>
          <tr>
            <th>Seam</th>
            <th>Windows</th>
            <th>Overlap</th>
            <th className="num">Days</th>
            <th className="num">Σ A</th>
            <th className="num">Σ B</th>
            <th className="num">Applied factor</th>
            <th className="num">Pair factor</th>
            <th>Guard</th>
          </tr>
        </thead>
        <tbody>
          {seams.map((s) => (
            <tr key={s.index} data-testid={`trends-seam-row-${s.index}`}>
              <td className="num">{s.index}</td>
              <td>
                {s.a_window + 1} → {s.b_window + 1}
              </td>
              <td>
                {s.overlap_start} → {s.overlap_end}
              </td>
              <td className="num">{s.overlap_days}</td>
              <td className="num">{num(s.sum_a, 1)}</td>
              <td className="num">{num(s.sum_b, 1)}</td>
              <td className="num">{num(s.applied_factor, 4)}</td>
              <td className="num">{num(s.pair_factor, 4)}</td>
              <td>
                {s.degenerate_guard ? (
                  <span className="badge badge-warn" style={{ color: theme.caution }}>
                    guard: f=1
                  </span>
                ) : (
                  DASH
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default TrendsSeamsTable;
