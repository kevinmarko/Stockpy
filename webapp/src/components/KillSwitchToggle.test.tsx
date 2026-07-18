/**
 * KillSwitchToggle.test.tsx — the shared pause/resume control for the ONE
 * global kill switch, rendered by BOTH Settings ("Signal generation") and the
 * Agentic Trading tab ("Controls"). Proves the reason gate, the advisory-only
 * resume block, the noun parameterization, and the honest error surface in
 * isolation, so the two screen tests don't each have to re-derive them.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KillSwitchToggle } from "./KillSwitchToggle";
import { api } from "../api/client";

type Overrides = {
  noun?: string;
  active?: boolean;
  reason?: string | null;
  advisoryOnly?: boolean;
  disabled?: boolean;
  showReason?: boolean;
};

function renderToggle(overrides: Overrides = {}) {
  const onChanged = vi.fn();
  const utils = render(
    <KillSwitchToggle
      noun={overrides.noun ?? "Signal generation"}
      active={overrides.active ?? false}
      reason={overrides.reason ?? null}
      advisoryOnly={overrides.advisoryOnly ?? true}
      onChanged={onChanged}
      disabled={overrides.disabled}
      showReason={overrides.showReason}
    />
  );
  return { onChanged, ...utils };
}

describe("KillSwitchToggle", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("pause: opens the confirm modal, requires a typed reason, then calls pauseAutomation", async () => {
    const user = userEvent.setup();
    const pauseSpy = vi
      .spyOn(api, "pauseAutomation")
      .mockResolvedValueOnce({ active: true, reason: "lunch break" });
    const { onChanged } = renderToggle({ active: false });

    await user.click(screen.getByRole("switch", { name: /Signal generation: Running/ }));
    expect(screen.getByText("Pause signal generation?")).toBeInTheDocument();

    // The reason is a required fat-finger guard: Pause stays disabled until typed.
    const pauseBtn = screen.getByRole("button", { name: "Pause" });
    expect(pauseBtn).toBeDisabled();

    await user.type(screen.getByLabelText("Reason"), "lunch break");
    expect(pauseBtn).not.toBeDisabled();
    await user.click(pauseBtn);

    await waitFor(() => expect(pauseSpy).toHaveBeenCalledWith("lunch break"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("resume: opens the confirm modal, requires a typed reason, then calls resumeAutomation", async () => {
    const user = userEvent.setup();
    const resumeSpy = vi
      .spyOn(api, "resumeAutomation")
      .mockResolvedValueOnce({ active: false, reason: null });
    const { onChanged } = renderToggle({ active: true, advisoryOnly: true });

    await user.click(screen.getByRole("switch", { name: /Signal generation: Paused/ }));
    expect(screen.getByText("Resume signal generation?")).toBeInTheDocument();

    const resumeBtn = screen.getByRole("button", { name: "Resume" });
    expect(resumeBtn).toBeDisabled();
    await user.type(screen.getByLabelText("Reason"), "back online");
    await user.click(resumeBtn);

    await waitFor(() => expect(resumeSpy).toHaveBeenCalledWith("back online"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("resume is disabled from the paused state when advisory_only is false", () => {
    renderToggle({ active: true, advisoryOnly: false });
    expect(screen.getByRole("switch", { name: /Signal generation: Paused/ })).toBeDisabled();
    expect(screen.getByText(/Resume must be done at the console/)).toBeInTheDocument();
  });

  it("the disabled prop forces the toggle off even while running (e.g. before status loads)", () => {
    renderToggle({ active: false, disabled: true });
    expect(screen.getByRole("switch", { name: /Signal generation: Running/ })).toBeDisabled();
  });

  it("the noun prop parameterizes the toggle label and the modal copy", async () => {
    const user = userEvent.setup();
    renderToggle({ noun: "Agent", active: false });
    await user.click(screen.getByRole("switch", { name: /Agent: Running/ }));
    expect(screen.getByText("Pause agent?")).toBeInTheDocument();
  });

  it("showReason renders the inline reason line only when paused with a reason", () => {
    renderToggle({ active: true, reason: "manual maintenance", showReason: true });
    expect(screen.getByText("Reason: manual maintenance")).toBeInTheDocument();
  });

  it("omits the inline reason when showReason is off (the reason is surfaced elsewhere)", () => {
    renderToggle({ active: true, reason: "manual maintenance", showReason: false });
    expect(screen.queryByText("Reason: manual maintenance")).not.toBeInTheDocument();
  });

  it("surfaces a pause failure's real error, never a fabricated success", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "pauseAutomation").mockRejectedValueOnce(
      new Error("Automation writes are disabled")
    );
    renderToggle({ active: false });

    await user.click(screen.getByRole("switch", { name: /Signal generation: Running/ }));
    await user.type(screen.getByLabelText("Reason"), "x");
    await user.click(screen.getByRole("button", { name: "Pause" }));

    expect(await screen.findByText("Automation writes are disabled")).toBeInTheDocument();
  });
});
