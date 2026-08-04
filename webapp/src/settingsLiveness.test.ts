import { describe, it, expect } from "vitest";
import type { TunableField, TunableLiveness } from "./api/types";
import {
  FALLBACK_LIVENESS,
  appliesBadge,
  buildConfirmMap,
  dangerousKeysIn,
  fieldBadge,
  isFieldEditable,
  resolveLiveness,
  saveOutcomeMessage,
  screenAppliesNotice,
} from "./settingsLiveness";

function lv(over: Partial<TunableLiveness> = {}): TunableLiveness {
  return { ...FALLBACK_LIVENESS, ...over };
}

function field(key: string, over: Partial<TunableField> = {}): TunableField {
  return {
    key,
    value: 1,
    type: "number",
    default: 1,
    description: null,
    ...over,
  };
}

describe("resolveLiveness", () => {
  it("returns the field's own liveness when present", () => {
    const f = field("A", { liveness: lv({ applies: "immediately" }) });
    expect(resolveLiveness(f).applies).toBe("immediately");
  });

  it("falls back to needs-a-restart, never to applies-immediately", () => {
    // The conservative direction: a field whose behaviour we cannot establish
    // must never be advertised as taking effect live.
    expect(resolveLiveness(field("A")).applies).toBe("next_daemon_restart");
  });

  it("fallback reports no capture sites and no restart reason rather than inventing either", () => {
    const r = resolveLiveness(field("A"));
    expect(r.capture_sites).toEqual([]);
    expect(r.restart_reason).toBeNull();
  });
});

describe("isFieldEditable", () => {
  it("blocks editing an env-pinned field", () => {
    expect(isFieldEditable(field("A", { liveness: lv({ env_pinned: true }) }))).toBe(false);
  });

  it("allows editing a no-effect field", () => {
    // Deliberate: the .env write is still durable, so a value a FUTURE build
    // reads can be set today. Only a shell pin makes editing pointless.
    expect(isFieldEditable(field("A", { liveness: lv({ applies: "no_effect" }) }))).toBe(true);
  });

  it("allows editing a dangerous field (it is gated by confirmation, not disabled)", () => {
    expect(isFieldEditable(field("A", { liveness: lv({ dangerous: true }) }))).toBe(true);
  });
});

describe("appliesBadge", () => {
  it("gives each state a distinct label and tone", () => {
    const states = ["immediately", "next_daemon_restart", "no_effect", "env_pinned"] as const;
    const labels = states.map((s) => appliesBadge(s).label);
    expect(new Set(labels).size).toBe(4);
    expect(appliesBadge("immediately").tone).toBe("good");
    expect(appliesBadge("env_pinned").tone).toBe("bad");
  });

  it("never advertises a live apply for a restart-required field", () => {
    expect(appliesBadge("next_daemon_restart").label).toMatch(/restart/i);
  });

  it("fieldBadge routes through the conservative fallback", () => {
    expect(fieldBadge(field("A")).label).toBe(appliesBadge("next_daemon_restart").label);
  });
});

describe("screenAppliesNotice", () => {
  it("does not claim a restart is needed when everything applies immediately", () => {
    const n = screenAppliesNotice("immediately");
    expect(n?.text).toMatch(/immediately/i);
    // It may SAY "no restart needed"; what it must never do is assert that a
    // restart is required, which is what the old blanket notice did here.
    expect(n?.text).toMatch(/no restart needed/i);
    expect(n?.text).not.toMatch(/until|picked up|needs a restart/i);
  });

  it("keeps the restart wording when every field needs one", () => {
    expect(screenAppliesNotice("next_daemon_restart")?.text).toMatch(/restart/i);
  });

  it("reports a mixed screen with real counts instead of one blanket claim", () => {
    const n = screenAppliesNotice("mixed", {
      immediately: 28,
      next_daemon_restart: 21,
      no_effect: 0,
      env_pinned: 0,
    });
    expect(n?.text).toContain("21");
    expect(n?.text).toMatch(/differ/i);
  });

  it("mentions env-pinned fields in a mixed screen", () => {
    const n = screenAppliesNotice("mixed", {
      immediately: 5,
      next_daemon_restart: 0,
      no_effect: 0,
      env_pinned: 2,
    });
    expect(n?.text).toMatch(/pinned/i);
    expect(n?.text).toContain("2");
  });

  it("warns rather than informs when nothing on the screen has any effect", () => {
    expect(screenAppliesNotice("no_effect")?.variant).toBe("warn");
  });

  it("falls back to the old restart wording when the backend sends no summary", () => {
    expect(screenAppliesNotice(undefined)?.text).toMatch(/restart/i);
  });
});

describe("dangerousKeysIn / buildConfirmMap", () => {
  const fields = [
    field("ADVISORY_ONLY", { liveness: lv({ dangerous: true }) }),
    field("DRY_RUN", { liveness: lv({ dangerous: true }) }),
    field("KELLY_FRACTION", { liveness: lv({ dangerous: false }) }),
  ];

  it("selects only the dangerous keys that are actually being written", () => {
    expect(dangerousKeysIn(fields, ["ADVISORY_ONLY", "KELLY_FRACTION"])).toEqual([
      "ADVISORY_ONLY",
    ]);
  });

  it("returns nothing when no dangerous key is in the batch", () => {
    expect(dangerousKeysIn(fields, ["KELLY_FRACTION"])).toEqual([]);
  });

  it("treats a field with no liveness as not dangerous (the server still gates it)", () => {
    expect(dangerousKeysIn([field("ADVISORY_ONLY")], ["ADVISORY_ONLY"])).toEqual([]);
  });

  it("builds a map echoing each key's own name — the shape the backend validates", () => {
    expect(buildConfirmMap(["ADVISORY_ONLY", "DRY_RUN"])).toEqual({
      ADVISORY_ONLY: "ADVISORY_ONLY",
      DRY_RUN: "DRY_RUN",
    });
  });
});

describe("saveOutcomeMessage", () => {
  it("says nothing when nothing was written", () => {
    expect(saveOutcomeMessage({ written: {} })).toBeNull();
  });

  it("prefers the server's own note, which is computed from the real outcome", () => {
    const m = saveOutcomeMessage({
      written: { A: 1 },
      per_key_applies: { A: "immediately" },
      note: "Saved to .env and applied to the running process — no restart needed.",
    });
    expect(m?.text).toContain("applied to the running process");
    expect(m?.variant).toBe("success");
  });

  it("does NOT claim a restart is pending when every key applied live", () => {
    const m = saveOutcomeMessage({
      written: { A: 1 },
      per_key_applies: { A: "immediately" },
    });
    expect(m?.text).not.toMatch(/until its next restart/i);
    expect(m?.variant).toBe("success");
  });

  it("keeps the restart wording when nothing applied live", () => {
    const m = saveOutcomeMessage({
      written: { A: 1 },
      per_key_applies: { A: "next_daemon_restart" },
    });
    expect(m?.text).toMatch(/restart/i);
    expect(m?.variant).toBe("info");
  });

  it("splits a mixed write instead of collapsing it into one generic sentence", () => {
    const m = saveOutcomeMessage({
      written: { A: 1, B: 2 },
      per_key_applies: { A: "immediately", B: "next_daemon_restart" },
    });
    expect(m?.text).toContain("A");
    expect(m?.text).toContain("B");
    expect(m?.text).toMatch(/Applied now/i);
    expect(m?.text).toMatch(/restart/i);
  });

  it("degrades to the restart wording when the backend sends no per-key outcome", () => {
    // No per_key_applies (older backend) => nothing is known to have applied
    // live, so the conservative message is the correct one.
    const m = saveOutcomeMessage({ written: { A: 1 } });
    expect(m?.text).toMatch(/restart/i);
  });
});
