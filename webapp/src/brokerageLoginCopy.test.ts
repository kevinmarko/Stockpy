import { describe, expect, it } from "vitest";
import { formatLoginCountdown, loginFailureMessage, PHASE_LABEL } from "./brokerageLoginCopy";
import type { BrokerageLoginJob } from "./api/types";

function job(overrides: Partial<BrokerageLoginJob> = {}): BrokerageLoginJob {
  return {
    job_id: "job-1",
    mode: "connect",
    state: "running",
    phase: "starting",
    error_code: null,
    seconds_remaining: 180,
    connected: false,
    has_account_snapshot: false,
    ...overrides,
  };
}

describe("formatLoginCountdown", () => {
  it("formats whole minutes", () => {
    expect(formatLoginCountdown(180)).toBe("3:00");
  });

  it("formats sub-minute remainders with zero padding", () => {
    expect(formatLoginCountdown(65)).toBe("1:05");
    expect(formatLoginCountdown(9)).toBe("0:09");
  });

  it("rounds a fractional value", () => {
    expect(formatLoginCountdown(59.6)).toBe("1:00");
  });

  it("clamps a negative value to 0:00 rather than showing a negative countdown", () => {
    expect(formatLoginCountdown(-5)).toBe("0:00");
  });
});

describe("PHASE_LABEL", () => {
  it("has a human label for every BrokerageLoginPhase", () => {
    expect(PHASE_LABEL.starting).toBeTruthy();
    expect(PHASE_LABEL.authenticating).toBeTruthy();
    expect(PHASE_LABEL.awaiting_approval).toBeTruthy();
    expect(PHASE_LABEL.verifying).toBeTruthy();
    expect(PHASE_LABEL.fetching_snapshot).toBeTruthy();
    expect(PHASE_LABEL.done).toBeTruthy();
  });
});

describe("loginFailureMessage", () => {
  it("null job -> a generic honest fallback", () => {
    expect(loginFailureMessage(null)).toMatch(/did not complete/i);
  });

  it("state: timeout -> never claims the operator denied the login", () => {
    const msg = loginFailureMessage(job({ state: "timeout", error_code: "timeout", seconds_remaining: 0 }));
    expect(msg).toMatch(/no approval came through in time/i);
    expect(msg).toMatch(/nothing was saved/i);
    expect(msg.toLowerCase()).not.toContain("denied");
  });

  it("state: cancelled -> honest cancellation copy", () => {
    const msg = loginFailureMessage(job({ state: "cancelled", error_code: "cancelled" }));
    expect(msg).toMatch(/cancelled/i);
    expect(msg).toMatch(/nothing was saved/i);
  });

  it.each([
    ["no_credentials", /no robinhood credentials were available/i],
    ["challenge_unsupported", /verification step this app doesn't support/i],
    ["auth_failed", /rejected that username or password/i],
    ["child_start_failed", /could not start the login process/i],
  ] as const)("state: failed, error_code: %s -> the specific reason", (errorCode, expected) => {
    const msg = loginFailureMessage(job({ state: "failed", error_code: errorCode }));
    expect(msg).toMatch(expected);
  });

  it("state: failed with an unrecognized/null error_code -> a generic honest fallback", () => {
    const msg = loginFailureMessage(job({ state: "failed", error_code: null }));
    expect(msg).toMatch(/did not complete/i);
  });
});
