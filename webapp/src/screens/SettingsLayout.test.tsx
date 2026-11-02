/**
 * SettingsLayout.test.tsx — the Settings shell must stay responsive.
 *
 * It used to be an inline-styled flex row with a hard-coded 250px sidebar and no
 * breakpoint, so on a 390px phone the content pane was squeezed to ~60px (one word per
 * line, clipped) and every Settings page scrolled sideways. Inline styles cannot carry a
 * media query, so the layout lives in index.css (.split-*); this pins that the component
 * uses those classes and does not hard-code a width again. jsdom has no layout engine, so
 * the stylesheet half is pinned in src/responsiveLayout.test.ts.
 */
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { SettingsLayout } from "./SettingsLayout";

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={["/settings"]}>
      <Routes>
        <Route path="/settings" element={<SettingsLayout />}>
          <Route index element={<div data-testid="pane-content">pane</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("SettingsLayout responsiveness", () => {
  it("lays out through the .split-* classes, not inline flex geometry", () => {
    const { container } = renderLayout();
    const pane = container.querySelector(".split-pane") as HTMLElement | null;
    expect(pane).not.toBeNull();
    expect(pane!.querySelector("nav.split-rail")).not.toBeNull();
    expect(pane!.querySelector(".split-content")).not.toBeNull();
    // The two things that caused the bug: a fixed sidebar width and a non-shrinking sidebar.
    const nav = pane!.querySelector("nav") as HTMLElement;
    expect(nav.style.width).toBe("");
    expect(nav.style.flexShrink).toBe("");
    expect(pane!.style.display).toBe("");
  });

  it("still renders every settings section link and the routed content", () => {
    const { container, getByTestId } = renderLayout();
    const links = Array.from(container.querySelectorAll("nav a")).map((a) => a.getAttribute("href"));
    expect(links).toEqual([
      "/settings",
      "/settings/data",
      "/settings/universe",
      "/settings/brokers",
      "/settings/modules",
      "/settings/feature-flags",
    ]);
    expect(getByTestId("pane-content")).toBeInTheDocument();
  });

  it("puts the links in one .split-rail-links strip under the heading, so mobile can scroll them", () => {
    const { container } = renderLayout();
    const nav = container.querySelector("nav.split-rail") as HTMLElement;
    const strip = nav.querySelector(":scope > .split-rail-links") as HTMLElement | null;
    expect(strip).not.toBeNull();
    // Heading stays a direct child of the rail (its own row ABOVE the strip); every link is in the strip.
    expect(Array.from(nav.children).map((c) => c.tagName)).toEqual(["H1", "DIV"]);
    expect(strip!.querySelectorAll(":scope > a")).toHaveLength(6);
    expect(nav.querySelectorAll(":scope > a")).toHaveLength(0);
  });

  it("scrolls the active link into the strip on mount without needing scrollIntoView", () => {
    // Renders under jsdom (no scrollIntoView, offsets are 0) without throwing, with the active link marked.
    const { container } = renderLayout();
    expect(container.querySelector(".split-rail-links a.active")?.getAttribute("href")).toBe("/settings");
    expect((container.querySelector(".split-rail-links") as HTMLElement).scrollLeft).toBe(0);
  });
});
