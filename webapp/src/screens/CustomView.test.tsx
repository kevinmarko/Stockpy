/**
 * CustomView.test.tsx
 *
 * Covers: an unknown slug renders an honest empty state (never a blank or
 * broken page), only the widgets a view was saved with actually render, the
 * chat widget opens the real global chat panel (via useChat().openChat, the
 * existing grounded/authed pattern -- not a bespoke endpoint), and deleting
 * removes the view.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CustomView } from "./CustomView";
import { __resetCustomViewsForTests, addOrUpdateView } from "../customViews";

const openChatMock = vi.fn();
vi.mock("../chat/ChatContext", () => ({
  useChat: () => ({ openChat: openChatMock, closeChat: vi.fn(), isOpen: false, contextText: undefined }),
}));

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
      widgets: { edgeByStrategy: true, symbolOverlay: false, aiChat: false },
    });
    renderAt("chart-only");

    expect(screen.getByText("Edge per strategy")).toBeInTheDocument();
    expect(screen.queryByText("Price history & signal overlay")).not.toBeInTheDocument();
    expect(screen.queryByTestId("custom-view-open-chat")).not.toBeInTheDocument();
  });

  it("the chat widget opens the real global chat panel with context naming the view", async () => {
    const user = userEvent.setup();
    addOrUpdateView({
      name: "Chatty View",
      widgets: { edgeByStrategy: false, symbolOverlay: false, aiChat: true },
    });
    renderAt("chatty-view");

    await user.click(screen.getByTestId("custom-view-open-chat"));
    expect(openChatMock).toHaveBeenCalledWith(expect.stringContaining("Chatty View"));
  });

  it("deleting the view removes it and navigates back to Create Data App", async () => {
    const user = userEvent.setup();
    addOrUpdateView({
      name: "Doomed",
      widgets: { edgeByStrategy: true, symbolOverlay: false, aiChat: false },
    });
    renderAt("doomed");

    await user.click(screen.getByTestId("custom-view-delete"));
    expect(await screen.findByTestId("create-data-app-screen")).toBeInTheDocument();

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(0);
  });
});
