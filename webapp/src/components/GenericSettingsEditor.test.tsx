/**
 * GenericSettingsEditor.test.tsx — the liveness/safety layer of the shared
 * settings-editor engine behind all five `/settings/*` screens.
 *
 * The engine's older behaviour (dirty-tracking, save-only-changed-keys, per-key
 * rejection, null-never-fabricated-as-zero) is covered through the screen tests
 * — SentimentSettings.test.tsx and SettingsManager.test.tsx. THIS file covers
 * what the per-field `liveness` metadata added:
 *
 *   - the per-field `applies` badge, replacing one blanket screen-wide claim;
 *   - an `env_pinned` field's input being genuinely disabled;
 *   - the `dangerous` confirmation flow actually BLOCKING a save and then
 *     allowing it — proven against ADVISORY_ONLY, the execution quarantine;
 *   - post-save feedback distinguishing "applied now" from "needs a restart".
 *
 * It renders through the real SettingsManager screen (rather than mounting the
 * component directly) so the props wiring each screen supplies is exercised too
 * — a badge that only works when a test constructs the props by hand would not
 * be worth much.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsManager } from "../screens/SettingsManager";
import { api } from "../api/client";
import type {
  TunableLiveness,
  TunablesResponse,
  TunablesUpdateResult,
} from "../api/types";

function lv(over: Partial<TunableLiveness> = {}): TunableLiveness {
  return {
    applies: "immediately",
    restart_reason: null,
    capture_sites: [],
    env_pinned: false,
    dangerous: false,
    source: "env_file",
    ...over,
  };
}

/**
 * A fixture spanning all four `applies` states plus a dangerous field, so one
 * render exercises every branch the UI has.
 */
function baseTunables(overrides: Partial<TunablesResponse> = {}): TunablesResponse {
  return {
    applies: "mixed",
    applies_counts: {
      immediately: 1,
      next_daemon_restart: 1,
      no_effect: 1,
      env_pinned: 1,
    },
    groups: [
      {
        name: "Position Sizing",
        fields: [
          {
            key: "KELLY_FRACTION",
            value: 0.5,
            type: "number",
            min: 0,
            max: 1,
            step: 0.05,
            default: 0.5,
            description: "Fraction of full Kelly.",
            liveness: lv({ applies: "immediately", source: "runtime_store" }),
          },
          {
            key: "MAX_POSITION_WEIGHT",
            value: 1,
            type: "number",
            min: 0,
            max: 2,
            step: 0.1,
            default: 1,
            description: "Per-name ceiling.",
            liveness: lv({
              applies: "next_daemon_restart",
              restart_reason:
                "This value was read once, when its module was first imported (execution/risk_gate.py:133).",
              capture_sites: ["execution/risk_gate.py:133"],
            }),
          },
        ],
      },
      {
        name: "Runtime & Ops",
        fields: [
          {
            key: "LOG_LEVEL",
            value: "INFO",
            type: "enum",
            options: ["DEBUG", "INFO", "WARNING", "ERROR"],
            default: "INFO",
            description: "Root log level.",
            liveness: lv({
              applies: "env_pinned",
              env_pinned: true,
              capture_sites: ["alerting.py:118"],
            }),
          },
          {
            key: "REQUIRED_RETURN_RATE",
            value: 0.08,
            type: "number",
            min: 0,
            max: 1,
            step: 0.01,
            default: 0.08,
            description: "Unused constant.",
            liveness: lv({ applies: "no_effect" }),
          },
          {
            key: "ADVISORY_ONLY",
            value: true,
            type: "boolean",
            default: true,
            description: "Execution quarantine — no order ever leaves the platform.",
            liveness: lv({
              applies: "next_daemon_restart",
              dangerous: true,
              restart_reason:
                "This value was read once, when its module was first imported (gui/app.py:249).",
              capture_sites: ["gui/app.py:249"],
            }),
          },
        ],
      },
    ],
    env_drift: { detected: false, keys: [], note: "" },
    ...overrides,
  };
}

function renderScreen() {
  return render(
    <MemoryRouter>
      <SettingsManager />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

// ---------------------------------------------------------------------------
// Per-field applies badge
// ---------------------------------------------------------------------------

describe("per-field applies badge", () => {
  it("renders a badge carrying each field's own applies state", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();

    expect(await screen.findByTestId("applies-badge-KELLY_FRACTION")).toHaveAttribute(
      "data-applies",
      "immediately",
    );
    expect(screen.getByTestId("applies-badge-MAX_POSITION_WEIGHT")).toHaveAttribute(
      "data-applies",
      "next_daemon_restart",
    );
    expect(screen.getByTestId("applies-badge-LOG_LEVEL")).toHaveAttribute(
      "data-applies",
      "env_pinned",
    );
    expect(screen.getByTestId("applies-badge-REQUIRED_RETURN_RATE")).toHaveAttribute(
      "data-applies",
      "no_effect",
    );
  });

  it("a live-safe field's badge does not tell the operator to restart", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    const badge = await screen.findByTestId("applies-badge-KELLY_FRACTION");
    expect(badge.textContent ?? "").not.toMatch(/restart/i);
  });

  it("surfaces the restart reason with its capture site, so the claim is checkable", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    const reason = await screen.findByTestId("restart-reason-MAX_POSITION_WEIGHT");
    expect(reason).toHaveTextContent("execution/risk_gate.py:133");
  });

  it("marks a dangerous field with its own badge", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    expect(await screen.findByTestId("dangerous-badge-ADVISORY_ONLY")).toBeInTheDocument();
    expect(screen.queryByTestId("dangerous-badge-KELLY_FRACTION")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Screen-level notice — no longer one blanket claim
// ---------------------------------------------------------------------------

describe("screen-level applies notice", () => {
  it("no longer asserts the old blanket restart claim on a mixed screen", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    const notice = await screen.findByTestId("applies-notice");
    expect(notice).not.toHaveTextContent(
      "Changes apply on the next pipeline / daemon restart (no hot-reload).",
    );
    // ...and instead describes this screen's actual mix.
    expect(notice).toHaveTextContent(/differ/i);
  });

  it("says changes apply immediately when every field does", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(
      baseTunables({
        applies: "immediately",
        applies_counts: {
          immediately: 2,
          next_daemon_restart: 0,
          no_effect: 0,
          env_pinned: 0,
        },
      }),
    );
    renderScreen();
    const notice = await screen.findByTestId("applies-notice");
    expect(notice).toHaveTextContent(/immediately/i);
    expect(notice).not.toHaveTextContent(/no hot-reload/i);
  });
});

// ---------------------------------------------------------------------------
// env_pinned — genuinely disabled, with an explanation
// ---------------------------------------------------------------------------

describe("env-pinned fields", () => {
  it("disables the input rather than merely labelling it", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    const select = (await screen.findByLabelText("LOG_LEVEL")) as HTMLSelectElement;
    expect(select).toBeDisabled();
  });

  it("explains WHY it is not editable", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    await screen.findByLabelText("LOG_LEVEL");
    expect(
      screen.getByText(/shell environment variable is set for LOG_LEVEL/i),
    ).toBeInTheDocument();
  });

  it("leaves a non-pinned field editable", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    expect(await screen.findByLabelText("KELLY_FRACTION")).not.toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// Dangerous-field confirmation — the core of this feature
// ---------------------------------------------------------------------------

describe("dangerous-field confirmation flow (ADVISORY_ONLY)", () => {
  async function startFlippingAdvisoryOnly() {
    const user = userEvent.setup();
    renderScreen();
    const toggle = await screen.findByRole("switch", { name: "ADVISORY_ONLY" });
    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: /Save/ }));
    return user;
  }

  it("BLOCKS the save and opens a confirmation instead of calling the API", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    const updateSpy = vi.spyOn(api, "updateTunables");
    await startFlippingAdvisoryOnly();

    expect(await screen.findByTestId("dangerous-confirm")).toBeInTheDocument();
    // The critical assertion: pressing Save did NOT submit anything.
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it("keeps the confirm button disabled until the field name is typed exactly", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    const user = await startFlippingAdvisoryOnly();
    await screen.findByTestId("dangerous-confirm");

    const confirmBtn = screen.getByTestId("dangerous-confirm-yes");
    expect(confirmBtn).toBeDisabled();

    const input = screen.getByLabelText('Type "ADVISORY_ONLY" to confirm');
    await user.type(input, "advisory_only");
    expect(confirmBtn).toBeDisabled();

    await user.clear(input);
    await user.type(input, "ADVISORY_ONLY");
    expect(confirmBtn).toBeEnabled();
  });

  it("cancelling submits nothing and leaves the change pending", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    const updateSpy = vi.spyOn(api, "updateTunables");
    const user = await startFlippingAdvisoryOnly();
    await screen.findByTestId("dangerous-confirm");

    await user.click(screen.getByTestId("dangerous-confirm-cancel"));

    await waitFor(() => expect(screen.queryByTestId("dangerous-confirm")).toBeNull());
    expect(updateSpy).not.toHaveBeenCalled();
    // Still dirty, so the operator can retry.
    expect(screen.getByRole("button", { name: /Save 1 change/ })).toBeEnabled();
  });

  it("ALLOWS the save once confirmed, sending the echo-the-name confirm map", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    const updateSpy = vi.spyOn(api, "updateTunables").mockResolvedValue({
      written: { ADVISORY_ONLY: false },
      rejected: {},
      applies: "next_daemon_restart",
      per_key_applies: { ADVISORY_ONLY: "next_daemon_restart" },
      restart_required: true,
      note: "Saved to .env. The running process keeps the previous values until it restarts (POST /daemon/restart).",
    });

    const user = await startFlippingAdvisoryOnly();
    await screen.findByTestId("dangerous-confirm");
    await user.type(
      screen.getByLabelText('Type "ADVISORY_ONLY" to confirm'),
      "ADVISORY_ONLY",
    );
    await user.click(screen.getByTestId("dangerous-confirm-yes"));

    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(updateSpy).toHaveBeenCalledWith(
      { ADVISORY_ONLY: false },
      { ADVISORY_ONLY: "ADVISORY_ONLY" },
    );
  });

  it("an ordinary field saves with no confirmation dialog at all", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    const updateSpy = vi.spyOn(api, "updateTunables").mockResolvedValue({
      written: { KELLY_FRACTION: 0.6 },
      rejected: {},
      applies: "immediately",
      per_key_applies: { KELLY_FRACTION: "immediately" },
      restart_required: false,
    });
    const user = userEvent.setup();
    renderScreen();

    const input = (await screen.findByLabelText("KELLY_FRACTION")) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "0.6");
    await user.click(screen.getByRole("button", { name: /Save/ }));

    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("dangerous-confirm")).toBeNull();
    expect(updateSpy).toHaveBeenCalledWith({ KELLY_FRACTION: 0.6 }, {});
  });

  it("surfaces the server's own rejection if a confirmation is somehow missing", async () => {
    // Defence in depth: the gate is enforced server-side, so even a UI bypass
    // must be reported honestly rather than read as success.
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    vi.spyOn(api, "updateTunables").mockResolvedValue({
      written: {},
      rejected: { ADVISORY_ONLY: "confirmation_required" },
      applies: "next_daemon_restart",
      per_key_applies: {},
    });

    const user = await startFlippingAdvisoryOnly();
    await screen.findByTestId("dangerous-confirm");
    await user.type(
      screen.getByLabelText('Type "ADVISORY_ONLY" to confirm'),
      "ADVISORY_ONLY",
    );
    await user.click(screen.getByTestId("dangerous-confirm-yes"));

    expect(await screen.findByTestId("rejected-ADVISORY_ONLY")).toHaveTextContent(
      /confirmation_required/,
    );
  });
});

// ---------------------------------------------------------------------------
// Post-save messaging — applied now vs. needs restart
// ---------------------------------------------------------------------------

describe("post-save feedback", () => {
  async function saveKellyFraction(result: TunablesUpdateResult) {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    vi.spyOn(api, "updateTunables").mockResolvedValue(result);
    const user = userEvent.setup();
    renderScreen();
    const input = (await screen.findByLabelText("KELLY_FRACTION")) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "0.6");
    await user.click(screen.getByRole("button", { name: /Save/ }));
  }

  it("says the change is live — NOT that a restart is pending — when it applied immediately", async () => {
    await saveKellyFraction({
      written: { KELLY_FRACTION: 0.6 },
      rejected: {},
      applies: "immediately",
      per_key_applies: { KELLY_FRACTION: "immediately" },
      restart_required: false,
      note: "Saved to .env and applied to the running process — no restart needed.",
    });
    const notice = await screen.findByTestId("written-notice");
    expect(notice).toHaveTextContent(/applied to the running process/i);
    // The old hardcoded sentence must not appear for a live-applied key.
    expect(notice).not.toHaveTextContent(
      /keeps the previous values until its next restart/i,
    );
  });

  it("keeps the restart wording when the write only reached .env", async () => {
    await saveKellyFraction({
      written: { KELLY_FRACTION: 0.6 },
      rejected: {},
      applies: "next_daemon_restart",
      per_key_applies: { KELLY_FRACTION: "next_daemon_restart" },
      restart_required: true,
      note: "Saved to .env. The running process keeps the previous values until it restarts (POST /daemon/restart).",
    });
    expect(await screen.findByTestId("written-notice")).toHaveTextContent(/restarts/i);
  });

  it("survives the post-save reload instead of flashing and vanishing", async () => {
    // Regression: a successful save calls reload(), and useApi.reload() sets
    // `loading`, which UNMOUNTS SettingsForm. When the result lived in that
    // component's own useMutation state it went down with it, so the operator
    // never got to read what happened. The result is owned by the parent for
    // exactly this reason.
    let resolveReload: (v: TunablesResponse) => void = () => {};
    const getSpy = vi
      .spyOn(api, "getTunables")
      .mockResolvedValueOnce(baseTunables())
      // The refetch triggered by the save — held open so the reload's
      // loading state is genuinely observed while we assert.
      .mockImplementationOnce(
        () => new Promise<TunablesResponse>((res) => (resolveReload = res)),
      );
    vi.spyOn(api, "updateTunables").mockResolvedValue({
      written: { KELLY_FRACTION: 0.6 },
      rejected: {},
      applies: "immediately",
      per_key_applies: { KELLY_FRACTION: "immediately" },
      restart_required: false,
      note: "Saved to .env and applied to the running process — no restart needed.",
    });

    const user = userEvent.setup();
    renderScreen();
    const input = (await screen.findByLabelText("KELLY_FRACTION")) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "0.6");
    await user.click(screen.getByRole("button", { name: /Save/ }));

    // Mid-reload: the form is unmounted, but the feedback must still be there.
    const notice = await screen.findByTestId("written-notice");
    expect(notice).toHaveTextContent(/applied to the running process/i);
    expect(screen.queryByLabelText("KELLY_FRACTION")).toBeNull();

    resolveReload(baseTunables());
    await waitFor(() => expect(screen.getByLabelText("KELLY_FRACTION")).toBeInTheDocument());
    // ...and still there after the reload completes.
    expect(screen.getByTestId("written-notice")).toBeInTheDocument();
    expect(getSpy).toHaveBeenCalledTimes(2);
  });

  it("keeps a per-key rejection visible across the same reload", async () => {
    let resolveReload: (v: TunablesResponse) => void = () => {};
    vi.spyOn(api, "getTunables")
      .mockResolvedValueOnce(baseTunables())
      .mockImplementationOnce(
        () => new Promise<TunablesResponse>((res) => (resolveReload = res)),
      );
    vi.spyOn(api, "updateTunables").mockResolvedValue({
      written: {},
      rejected: { KELLY_FRACTION: "out_of_range" },
      applies: "next_daemon_restart",
      per_key_applies: {},
    });

    const user = userEvent.setup();
    renderScreen();
    const input = (await screen.findByLabelText("KELLY_FRACTION")) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "0.6");
    await user.click(screen.getByRole("button", { name: /Save/ }));

    await screen.findByTestId("rejected-notice");
    resolveReload(baseTunables());
    expect(await screen.findByTestId("rejected-KELLY_FRACTION")).toHaveTextContent(
      /out_of_range/,
    );
  });

  it("reports a mixed write as both, not as one generic message", async () => {
    await saveKellyFraction({
      written: { KELLY_FRACTION: 0.6, MAX_POSITION_WEIGHT: 1.2 },
      rejected: {},
      applies: "mixed",
      per_key_applies: {
        KELLY_FRACTION: "immediately",
        MAX_POSITION_WEIGHT: "next_daemon_restart",
      },
      restart_required: true,
      note: "Saved to .env. 1 applied to the running process immediately; 1 take effect on the next restart (MAX_POSITION_WEIGHT).",
    });
    const notice = await screen.findByTestId("written-notice");
    expect(notice).toHaveTextContent(/applied to the running process immediately/i);
    expect(notice).toHaveTextContent(/MAX_POSITION_WEIGHT/);
  });
});
