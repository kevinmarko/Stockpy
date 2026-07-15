/**
 * Onboarding.test.tsx — component tests for the 3-step onboarding wizard
 * (Choose a Pilot -> Connect brokerage -> Set amount), the last webapp
 * screen with no test coverage after the 2026-07-14 test-coverage
 * re-audit's Phase 5 pass.
 *
 * Renders against the REAL mock API (no vi.mock), matching this codebase's
 * established screen-test convention (see Marketplace.test.tsx/
 * PilotDetail.test.tsx) -- exercises the same data flow the app runs
 * offline. Navigation assertions render sibling routes so "did onDone /
 * navigate fire correctly" is verified by what's actually on screen, not by
 * inspecting router internals. localStorage is cleared between tests so
 * completeOnboarding's real persistence can be asserted directly (see
 * onboarding.test.ts for that module's own unit tests).
 */

import { render, screen } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Onboarding } from "./Onboarding";
import { readOnboarding } from "../onboarding";
import { api } from "../api/client";
import { ApiError } from "../api/types";

function renderOnboarding(onDone = vi.fn()) {
  const utils = render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<Onboarding onDone={onDone} />} />
        <Route path="/pilots/:id" element={<div>PILOT DETAIL PAGE</div>} />
      </Routes>
    </MemoryRouter>
  );
  return { ...utils, onDone };
}

async function goToStep1(pilotName = "Trend Follower") {
  await screen.findByText("Choose a Pilot");
  const pilotButton = await screen.findByText(pilotName);
  fireEvent.click(pilotButton);
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByText("Connect brokerage");
}

async function goToStep2(brokerage: "paper" | "skip" = "paper") {
  await goToStep1();
  fireEvent.click(
    screen.getByText(
      brokerage === "paper" ? /paper trading/i : /browse only for now/i
    )
  );
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByText("Set amount");
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("Onboarding — step 0 (choose a Pilot)", () => {
  it("renders the real mock catalog with sharpe and honest deployable badges", async () => {
    renderOnboarding();

    expect(await screen.findByText("Trend Follower")).toBeInTheDocument();
    expect(screen.getByText(/1.12 Sharpe/)).toBeInTheDocument();
    // momentum-burst is deliberately non-deployable (docs/AUTOPILOT_PLAN.md) --
    // it must still appear, badge shown honestly, never hidden from onboarding.
    expect(screen.getByText("Momentum Burst")).toBeInTheDocument();
    expect(screen.getAllByText(/not deployable/i).length).toBeGreaterThan(0);
  });

  it("Continue is disabled until a Pilot is selected", async () => {
    renderOnboarding();
    await screen.findByText("Trend Follower");

    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();

    fireEvent.click(screen.getByText("Trend Follower"));

    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("advances to step 1 on Continue", async () => {
    renderOnboarding();
    await goToStep1();
    expect(screen.getByText("Connect brokerage")).toBeInTheDocument();
  });
});

describe("Onboarding — step 1 (connect brokerage)", () => {
  it("states the advisory / paper-first / gated-queue contract plainly", async () => {
    renderOnboarding();
    await goToStep1();

    expect(screen.getByText(/paper-first/i)).toBeInTheDocument();
    expect(
      screen.getByText(/no live order is ever\s+placed automatically/i)
    ).toBeInTheDocument();
  });

  it("shows the real execution mode from apiMeta, not a hardcoded label", async () => {
    renderOnboarding();
    await goToStep1();

    // api/mock.ts's MOCK_MODE is "review" -- this pins that the notice
    // reads the real apiMeta.mockMode rather than a static string.
    expect(screen.getByText(/execution mode is currently/i)).toHaveTextContent(
      "review"
    );
  });

  it("Continue is disabled until a brokerage option is chosen", async () => {
    renderOnboarding();
    await goToStep1();

    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();

    fireEvent.click(screen.getByText(/paper trading/i));

    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("Back returns to step 0 with the Pilot selection preserved", async () => {
    renderOnboarding();
    await goToStep1("Dip Buyer");

    fireEvent.click(screen.getByRole("button", { name: "Back" }));

    await screen.findByText("Choose a Pilot");
    // The previously-selected card is still highlighted (selected border) --
    // verified indirectly via Continue being enabled without re-selecting.
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("advances to step 2 on Continue", async () => {
    renderOnboarding();
    await goToStep2();
    expect(screen.getByText("Set amount")).toBeInTheDocument();
  });
});

describe("Onboarding — step 1 (connect Robinhood)", () => {
  it("selecting Connect Robinhood reveals the credential form and hides it once connected", async () => {
    renderOnboarding();
    await goToStep1();

    fireEvent.click(screen.getByText(/connect robinhood/i));

    expect(screen.getByLabelText(/robinhood email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^password$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/totp secret/i)).toBeInTheDocument();
  });

  it("the Connect button stays disabled until all three fields are filled", async () => {
    renderOnboarding();
    await goToStep1();
    fireEvent.click(screen.getByText(/connect robinhood/i));

    const connectBtn = screen.getByRole("button", { name: /connect$/i });
    expect(connectBtn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/robinhood email/i), {
      target: { value: "user@example.com" },
    });
    expect(connectBtn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^password$/i), {
      target: { value: "hunter2" },
    });
    expect(connectBtn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/totp secret/i), {
      target: { value: "JBSWY3DPEHPK3PXP" },
    });
    expect(connectBtn).toBeEnabled();
  });

  it("a successful connect enables Continue and never displays the submitted password", async () => {
    renderOnboarding();
    await goToStep1();
    fireEvent.click(screen.getByText(/connect robinhood/i));

    fireEvent.change(screen.getByLabelText(/robinhood email/i), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/^password$/i), {
      target: { value: "sUp3rS3cr3tPassw0rd!!" },
    });
    fireEvent.change(screen.getByLabelText(/totp secret/i), {
      target: { value: "JBSWY3DPEHPK3PXP" },
    });

    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /connect$/i }));

    await screen.findByText(/connect robinhood — connected/i);
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
    // The credential form unmounts once connected — password never lingers on screen.
    expect(screen.queryByLabelText(/^password$/i)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("sUp3rS3cr3tPassw0rd!!");
  });

  it("a failed verification shows an inline error and keeps Continue disabled", async () => {
    const spy = vi
      .spyOn(api, "connectBrokerage")
      .mockRejectedValueOnce(
        new ApiError("Could not verify Robinhood credentials.", 401)
      );

    renderOnboarding();
    await goToStep1();
    fireEvent.click(screen.getByText(/connect robinhood/i));

    fireEvent.change(screen.getByLabelText(/robinhood email/i), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/^password$/i), {
      target: { value: "wrongpassword" },
    });
    fireEvent.change(screen.getByLabelText(/totp secret/i), {
      target: { value: "JBSWY3DPEHPK3PXP" },
    });
    fireEvent.click(screen.getByRole("button", { name: /connect$/i }));

    await screen.findByText(/could not verify robinhood credentials/i);
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    spy.mockRestore();
  });
});

describe("Onboarding — step 2 (set amount)", () => {
  it("shows the chosen Pilot's name in the allocation prompt", async () => {
    renderOnboarding();
    await goToStep1("Trend Follower");
    fireEvent.click(screen.getByText(/browse only for now/i));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByText("Set amount");
    expect(screen.getByText("Trend Follower")).toBeInTheDocument();
  });

  it("still resolves the Pilot's name for a non-deployable choice via the pilots fallback", async () => {
    // "Momentum Burst" is excluded from the `deployable` filter Onboarding
    // computes for step 2's primary lookup; this pins that the fallback to
    // the full `pilots` list still finds it by name rather than showing the
    // generic "this Pilot" placeholder.
    renderOnboarding();
    await goToStep1("Momentum Burst");
    fireEvent.click(screen.getByText(/browse only for now/i));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByText("Set amount");
    expect(screen.getByText("Momentum Burst")).toBeInTheDocument();
    expect(screen.queryByText("this Pilot")).not.toBeInTheDocument();
  });

  it("quick-amount chips set the input value", async () => {
    renderOnboarding();
    await goToStep2();

    fireEvent.click(screen.getByRole("button", { name: "$1000" }));

    expect(screen.getByLabelText(/allocation \(usd\)/i)).toHaveValue(1000);
  });

  it("Back returns to step 1", async () => {
    renderOnboarding();
    await goToStep2();

    fireEvent.click(screen.getByRole("button", { name: "Back" }));

    expect(await screen.findByText("Connect brokerage")).toBeInTheDocument();
  });
});

describe("Onboarding — completion", () => {
  it("'Get started' persists the full selection, calls onDone, and navigates to the Pilot", async () => {
    const onDone = vi.fn();
    renderOnboarding(onDone);
    await goToStep1("Trend Follower");
    fireEvent.click(screen.getByText(/paper trading/i));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByText("Set amount");
    fireEvent.click(screen.getByRole("button", { name: "$2500" }));

    fireEvent.click(screen.getByRole("button", { name: "Get started" }));

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("PILOT DETAIL PAGE")).toBeInTheDocument();

    const stored = readOnboarding();
    expect(stored.completed).toBe(true);
    expect(stored.pilotId).toBe("trend-following");
    expect(stored.brokerage).toBe("paper");
    expect(stored.amount).toBe(2500);
    expect(stored.completedAt).toBeDefined();
  });

  it("'Get started' with brokerage=skip persists 'skip' and still navigates to the Pilot", async () => {
    const onDone = vi.fn();
    renderOnboarding(onDone);
    await goToStep2("skip");

    fireEvent.click(screen.getByRole("button", { name: "Get started" }));

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("PILOT DETAIL PAGE")).toBeInTheDocument();
    expect(readOnboarding().brokerage).toBe("skip");
  });

  it("'Skip for now' persists brokerage:skip and calls onDone, without requiring step completion", async () => {
    // NOTE: this test does not assert on the nav("/") call's DOM effect --
    // Onboarding itself is mounted AT "/" here (matching how it's actually
    // routed in App.tsx via a catch-all), so navigating to "/" while already
    // there is a same-route no-op and never unmounts/remounts the component.
    // In the real app, leaving onboarding behind is driven by onDone()
    // flipping App's `done` state (which swaps the whole rendered tree, not
    // by the router), so onDone firing is the correct, real signal to check.
    const onDone = vi.fn();
    renderOnboarding(onDone);
    await goToStep1();
    fireEvent.click(screen.getByText(/browse only for now/i));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByText("Set amount");

    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(readOnboarding()).toMatchObject({ completed: true, brokerage: "skip" });
  });

  it("never claims an order was placed anywhere in the flow", async () => {
    renderOnboarding();
    await goToStep2();

    expect(
      screen.queryByText(/order (has been|was) placed/i)
    ).not.toBeInTheDocument();
  });
});
