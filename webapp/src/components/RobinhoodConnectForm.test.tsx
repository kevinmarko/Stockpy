/**
 * RobinhoodConnectForm.test.tsx — the shared credential-intake form for the
 * async device-approval PUSH Robinhood login (POST /brokerage/connect,
 * polled via GET /brokerage/login/status/{job_id}), rendered by Onboarding,
 * Settings, and the Agentic Trading auth modal. Covers the state machine
 * (idle -> running -> connected | timeout | failed | cancelled) in
 * isolation so the three screen tests don't each have to re-derive it.
 *
 * Real timers by default: the first `connectBrokerage()` response resolves
 * via a plain microtask, so `screen.findByText` works normally. Only the
 * tests that need to observe a SECOND/THIRD poll (2s apart) switch to fake
 * timers locally -- and once fake timers are active, they use synchronous
 * `getByText`/`queryByText` rather than `findByText`, since Testing
 * Library's `findBy*`/`waitFor` retry loop is itself `setTimeout`-based and
 * hangs forever against a frozen fake clock nothing else is advancing (the
 * same trap documented in TopStatusBar.test.tsx's fake-timer describe block).
 */
import { act, render, screen } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RobinhoodConnectForm } from "./RobinhoodConnectForm";
import { api } from "../api/client";
import type { BrokerageLoginJob } from "../api/types";

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

async function fillAndSubmit() {
  fireEvent.change(screen.getByLabelText(/robinhood email/i), {
    target: { value: "trader@example.com" },
  });
  fireEvent.change(screen.getByLabelText(/^password$/i), {
    target: { value: "hunter2" },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
  });
}

describe("RobinhoodConnectForm", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("renders username/password only -- no MFA/authenticator field", () => {
    render(<RobinhoodConnectForm />);
    expect(screen.getByLabelText(/robinhood email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^password$/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/authenticator/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/mfa/i)).not.toBeInTheDocument();
  });

  it("Connect stays disabled until both fields are filled", () => {
    render(<RobinhoodConnectForm />);
    const btn = screen.getByRole("button", { name: /^connect$/i });
    expect(btn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/robinhood email/i), {
      target: { value: "trader@example.com" },
    });
    expect(btn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^password$/i), {
      target: { value: "hunter2" },
    });
    expect(btn).not.toBeDisabled();
  });

  it("idle -> submit -> running: calls connectBrokerage with no mfa_code and shows the approval prompt", async () => {
    const connectSpy = vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(job());
    render(<RobinhoodConnectForm />);

    await fillAndSubmit();

    expect(connectSpy).toHaveBeenCalledWith({
      username: "trader@example.com",
      password: "hunter2",
    });
    expect(
      await screen.findByText(/open the robinhood app and approve this login/i)
    ).toBeInTheDocument();
    // The fields (and the submitted password) are gone while running.
    expect(screen.queryByLabelText(/^password$/i)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("hunter2");
  });

  it("shows a live countdown from seconds_remaining, re-synced on each poll", async () => {
    // Fake timers from BEFORE the component ever mounts: usePoll's
    // setInterval is created the instant the job goes "running" (inside
    // fillAndSubmit below), and vi.useFakeTimers() only intercepts
    // setInterval/setTimeout calls made AFTER it's installed -- switching to
    // fake timers later would leave that interval running on the real clock,
    // unaffected by vi.advanceTimersByTimeAsync.
    vi.useFakeTimers();
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(
      job({ phase: "awaiting_approval", seconds_remaining: 180 })
    );
    vi.spyOn(api, "getBrokerageLoginStatus").mockResolvedValueOnce(
      job({ phase: "awaiting_approval", seconds_remaining: 120 })
    );
    render(<RobinhoodConnectForm />);
    await fillAndSubmit();
    expect(screen.getByText(/3:00 remaining/)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByText(/2:00 remaining/)).toBeInTheDocument();
  });

  it("a full successful poll sequence ends in the connected notice and calls onConnected once", async () => {
    vi.useFakeTimers(); // see the countdown test's comment above
    const onConnected = vi.fn();
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(
      job({ phase: "starting", seconds_remaining: 180 })
    );
    vi.spyOn(api, "getBrokerageLoginStatus")
      .mockResolvedValueOnce(job({ phase: "awaiting_approval", seconds_remaining: 178 }))
      .mockResolvedValueOnce(job({ phase: "verifying", seconds_remaining: 176 }))
      .mockResolvedValueOnce(
        job({
          state: "succeeded",
          phase: "done",
          seconds_remaining: 174,
          connected: true,
          has_account_snapshot: true,
        })
      );

    render(<RobinhoodConnectForm onConnected={onConnected} />);
    await fillAndSubmit();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000); // -> awaiting_approval
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000); // -> verifying
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000); // -> succeeded
    });

    expect(screen.getByText(/connected — approved in the robinhood app/i)).toBeInTheDocument();
    expect(onConnected).toHaveBeenCalledTimes(1);
  });

  it("Cancel calls cancelBrokerageLogin and returns to the idle form with an honest notice", async () => {
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(job());
    const cancelSpy = vi.spyOn(api, "cancelBrokerageLogin").mockResolvedValueOnce({
      ...job({ state: "cancelled", error_code: "cancelled" }),
      cancelled: true,
    });
    render(<RobinhoodConnectForm />);
    await fillAndSubmit();
    expect(await screen.findByText(/open the robinhood app and approve this login/i)).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    });

    expect(cancelSpy).toHaveBeenCalledWith("job-1");
    expect(await screen.findByText(/login cancelled\. nothing was saved\./i)).toBeInTheDocument();
    // Back to the editable form, ready to retry.
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/robinhood email/i)).toBeInTheDocument();
  });

  it("the timeout path shows honest copy -- never claims the operator denied the login", async () => {
    vi.useFakeTimers(); // see the countdown test's comment above
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(job());
    vi.spyOn(api, "getBrokerageLoginStatus").mockResolvedValueOnce(
      job({ state: "timeout", phase: "awaiting_approval", error_code: "timeout", seconds_remaining: 0 })
    );
    render(<RobinhoodConnectForm />);
    await fillAndSubmit();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(
      screen.getByText(/no approval came through in time\. nothing was saved\./i)
    ).toBeInTheDocument();
    expect(screen.queryByText(/denied/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("a failed job (auth_failed) shows the specific honest reason and allows retry with the same fields", async () => {
    vi.useFakeTimers(); // see the countdown test's comment above
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(job());
    vi.spyOn(api, "getBrokerageLoginStatus").mockResolvedValueOnce(
      job({ state: "failed", phase: "authenticating", error_code: "auth_failed" })
    );
    render(<RobinhoodConnectForm />);
    await fillAndSubmit();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(
      screen.getByText(/robinhood rejected that username or password/i)
    ).toBeInTheDocument();
    // Fields are back, still populated, ready for a retry submit.
    expect(screen.getByLabelText(/robinhood email/i)).toHaveValue("trader@example.com");
  });

  it("renders an optional title/subtitle when provided (modal usage)", () => {
    render(<RobinhoodConnectForm title="Connect Robinhood" subtitle="Approve in the app." />);
    expect(screen.getByText("Connect Robinhood")).toBeInTheDocument();
    expect(screen.getByText("Approve in the app.")).toBeInTheDocument();
  });
});
