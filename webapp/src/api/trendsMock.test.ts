/**
 * trendsMock.test.ts — the Google Trends mock fixtures stay honest and
 * internally consistent (offline, deterministic; no network).
 */
import { describe, expect, it } from "vitest";
import { mockApi } from "./mock";

describe("trends mock", () => {
  it("AAL seams are consistent: rescaled[k] = raw[k] * applied_factor", async () => {
    const r = await mockApi.getTrendsSvi("AAL");
    expect(r.status).toBe("ok");
    expect(r.raw_windows).toHaveLength(6);
    expect(r.seams).toHaveLength(5);
    for (const seam of r.seams) {
      const raw = r.raw_windows[seam.index].points;
      const resc = r.rescaled_windows[seam.index].points;
      expect(resc).toHaveLength(raw.length);
      expect(seam.applied_factor).not.toBeNull();
      raw.forEach(([ms, v], i) => {
        expect(resc[i][0]).toBe(ms);
        expect(Math.abs((resc[i][1] as number) - (v as number) * (seam.applied_factor as number))).toBeLessThan(1e-9);
      });
      expect(seam.degenerate_guard).toBe(false);
      expect(seam.pair_factor).not.toBeNull();
    }
    expect(r.recompute_check?.matches).toBe(true);
    // Warm-up ASVI points are null (not 0).
    expect(r.asvi?.points.slice(0, 7).every((p) => p[1] === null)).toBe(true);
    expect(r.asvi?.latest).not.toBeNull();
  });

  it("each mock term returns its contract status", async () => {
    const expected: Record<string, string> = {
      AAL: "ok",
      ABR: "ok",
      AGNC: "stitched_only",
      AM: "ok",
      AQN: "ok",
      ARCC: "partial_only",
      ZZZZ: "disabled",
      ERR: "error",
      QQQQ: "no_data",
    };
    for (const [term, status] of Object.entries(expected)) {
      expect((await mockApi.getTrendsSvi(term)).status).toBe(status);
    }
    expect((await mockApi.getTrendsSvi("zzzz")).enabled).toBe(false);
    expect((await mockApi.getTrendsSvi("ABR")).batch?.stale).toBe(true);
    const am = await mockApi.getTrendsSvi("AM");
    expect(am.recompute_check?.matches).toBe(false);
    expect(am.recompute_check?.max_abs_diff).toBe(4.2);
  });

  it("degenerate seam and nulls are honest (null, never 0)", async () => {
    const aqn = await mockApi.getTrendsSvi("AQN");
    const s = aqn.seams[0];
    expect(s.degenerate_guard).toBe(true);
    expect(s.applied_factor).toBe(1);
    expect(s.pair_factor).toBeNull();
    expect(aqn.seams.slice(1).every((x) => !x.degenerate_guard)).toBe(true);

    const agnc = await mockApi.getTrendsSvi("AGNC");
    expect(agnc.batch).toBeNull();
    expect(agnc.reason).toBeTruthy();
    expect(agnc.raw_windows).toEqual([]);
    expect(agnc.seams).toEqual([]);
    expect(agnc.recompute_check).toBeNull();

    const arcc = await mockApi.getTrendsSvi("ARCC");
    expect(arcc.batch).toBeNull();
    expect(arcc.stitched).toBeNull();
    expect(arcc.asvi).toBeNull();
    expect(arcc.latest_attempt?.status).toBe("partial");
    expect(arcc.latest_attempt?.reason).toBe("3 of 6 windows returned");
  });

  it("terms listing matches the contract", async () => {
    const t = await mockApi.getTrendsTerms();
    expect(t.status).toBe("ok");
    expect(t.tracked_universe_size).toBe(12);
    expect(t.tracked_with_data).toBe(5);
    expect(t.terms.map((x) => x.term)).toEqual(["AAL", "ABR", "AGNC", "AM", "AQN", "ARCC"]);
    const arcc = t.terms.find((x) => x.term === "ARCC")!;
    expect(arcc.stitched_days).toBe(0);
    expect(arcc.last_date).toBeNull();
    expect(arcc.latest_asvi).toBeNull();
    expect(arcc.has_complete_batch).toBe(false);
    expect(t.terms.find((x) => x.term === "ABR")!.stale).toBe(true);
    expect(t.terms.find((x) => x.term === "AAL")!.stale).toBe(false);
  });
});
