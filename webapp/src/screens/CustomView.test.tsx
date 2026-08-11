/**
 * CustomView.test.tsx
 *
 * Covers: an unknown slug renders an honest empty state (never a blank or
 * broken page), only the widgets a view was saved with actually render, the
 * chat widget opens the real global chat panel (via useChat().openChat, the
 * existing grounded/authed pattern -- not a bespoke endpoint), deleting
 * removes the view, and -- a code-review finding -- the view disappearing
 * out from under an already-mounted page via an EXTERNAL write (not this
 * component's own Delete button) degrades to the same honest empty state
 * rather than crashing.
 */
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CustomView } from "./CustomView";
import { __resetCustomViewsForTests, addOrUpdateView, removeView, type CustomViewWidgets } from "../customViews";

const openChatMock = vi.fn();
vi.mock("../chat/ChatContext", () => ({
  useChat: () => ({ openChat: openChatMock, closeChat: vi.fn(), isOpen: false, contextText: undefined }),
}));

function widgets(overrides: Partial<CustomViewWidgets> = {}): CustomViewWidgets {
  return {
    edgeByStrategy: false,
    symbolOverlay: false,
    aiChat: false,
    pilotsTable: false,
    sentimentMini: false,
    portfolioHeat: false,
    optionsDirective: false,
    signalBreakdown: false,
    macroRegime: false,
    ...overrides,
  };
}

function renderAt(slug: string) {
  return render(
    <MemoryRouter initialEntries={[`/app/${slug}`]}>
      <Routes>
        <Route path="/app/:slug" element={<CustomView />} />
        <Route path="/create-data-app" element={<div data-testid="create-data-app-screen" />} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  __resetCustomViewsForTests();
  openChatMock.mockClear();
});

describe("CustomView screen", () => {
  it("an unknown slug renders an honest empty state, not a blank page", () => {
    renderAt("does-not-exist");
    expect(screen.getByText("Data App not found")).toBeInTheDocument();
    expect(screen.getByText("No Data App saved at this address")).toBeInTheDocument();
  });

  it("renders only the widgets the view was saved with", () => {
    addOrUpdateView({
      name: "Chart Only",
      widgets: widgets({ edgeByStrategy: true }),
    });
    renderAt("chart-only");

    expect(screen.getByText("Edge per strategy")).toBeInTheDocument();
    expect(screen.queryByText("Price history & signal overlay")).not.toBeInTheDocument();
    expect(screen.queryByTestId("custom-view-open-chat")).not.toBeInTheDocument();
  });

  it("renders each of the 6 additional widget sections when enabled", () => {
    addOrUpdateView({
      name: "Everything",
      widgets: widgets({
        pilotsTable: true,
        sentimentMini: true,
        portfolioHeat: true,
        optionsDirective: true,
        signalBreakdown: true,
        macroRegime: true,
      }),
    });
    renderAt("everything");

    for (const heading of [
      "Pilots",
      "Sentiment history",
      "Portfolio heat",
      "Options directives",
      "Signal breakdown",
      "Macro regime",
    ]) {
      expect(screen.getByText(heading)).toBeInTheDocument();
    }
  });

  it("the chat widget opens the real global chat panel with context naming the view", async () => {
    const user = userEvent.setup();
    addOrUpdateView({
      name: "Chatty View",
      widgets: widgets({ aiChat: true }),
    });
    renderAt("chatty-view");

    await user.click(screen.getByTestId("custom-view-open-chat"));
    expect(openChatMock).toHaveBeenCalledWith(expect.stringContaining("Chatty View"));
  });

  it("deleting the view removes it and navigates back to Create Data App", async () => {
    const user = userEvent.setup();
    addOrUpdateView({
      name: "Doomed",
      widgets: widgets({ edgeByStrategy: true }),
    });
    renderAt("doomed");

    await user.click(screen.getByTestId("custom-view-delete"));
    expect(await screen.findByTestId("create-data-app-screen")).toBeInTheDocument();

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(0);
  });

  it("REGRESSION (review finding): the view disappearing via an EXTERNAL write (not this component's own Delete button) falls back to the honest empty state instead of crashing", async () => {
    const { view } = addOrUpdateView({
      name: "Deleted Elsewhere",
      widgets: widgets({ edgeByStrategy: true }),
    });
    renderAt("deleted-elsewhere");
    expect(screen.getByText("Edge per strategy")).toBeInTheDocument();

    // Simulate a delete performed from a second mounted instance / another
    // browser tab -- NOT this component's own handleDelete click handler.
    // useCustomViews() is a live useSyncExternalStore subscription, so the
    // already-mounted CustomView must re-render and fall through to its
    // not-found branch rather than crash on `view.widgets.*` once `view`
    // becomes undefined.
    act(() => {
      removeView(view.id);
    });

    expect(screen.getByText("Data App not found")).toBeInTheDocument();
    expect(screen.queryByText("Edge per strategy")).not.toBeInTheDocument();
  });
});
