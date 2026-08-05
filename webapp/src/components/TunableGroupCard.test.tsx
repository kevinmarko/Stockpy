/**
 * TunableGroupCard.test.tsx — the per-group collapsible wrapper every
 * settings screen (via GenericSettingsEditor) renders one of per tunable
 * group. Covers the open/close LOGIC only (defaultOpen, the header toggle,
 * the dirty/rejected badges, the fields.length===0 early return) -- the
 * framer-motion expand/collapse wrapper around the content is a purely
 * visual addition and isn't itself asserted on here (jsdom doesn't run real
 * animations), only that the content ends up present/absent as expected
 * once the state settles.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TunableGroupCard } from "./TunableGroupCard";
import type { TunableField } from "../api/types";

function field(key: string): TunableField {
  return {
    key,
    value: 1,
    type: "number",
    default: 1,
    description: null,
  };
}

describe("TunableGroupCard", () => {
  it("renders closed by default (content absent) unless defaultOpen is true", async () => {
    render(
      <TunableGroupCard name="Sizing" fields={[field("KELLY_FRACTION")]}>
        <div>Field content</div>
      </TunableGroupCard>
    );

    expect(screen.queryByText("Field content")).not.toBeInTheDocument();
    expect(screen.getByTestId("group-header-sizing")).toHaveTextContent("Expand");
  });

  it("defaultOpen renders the content immediately", async () => {
    render(
      <TunableGroupCard name="Sizing" fields={[field("KELLY_FRACTION")]} defaultOpen>
        <div>Field content</div>
      </TunableGroupCard>
    );

    expect(await screen.findByText("Field content")).toBeInTheDocument();
    expect(screen.getByTestId("group-header-sizing")).toHaveTextContent("Collapse");
  });

  it("clicking the header toggles open/closed", async () => {
    const user = userEvent.setup();
    render(
      <TunableGroupCard name="Sizing" fields={[field("KELLY_FRACTION")]}>
        <div>Field content</div>
      </TunableGroupCard>
    );

    const header = screen.getByTestId("group-header-sizing");
    expect(screen.queryByText("Field content")).not.toBeInTheDocument();

    await user.click(header);
    expect(await screen.findByText("Field content")).toBeInTheDocument();
    expect(header).toHaveTextContent("Collapse");

    await user.click(header);
    await waitFor(() =>
      expect(screen.queryByText("Field content")).not.toBeInTheDocument()
    );
    expect(header).toHaveTextContent("Expand");
  });

  it("renders the dirtyCount badge only when > 0", () => {
    const { rerender } = render(
      <TunableGroupCard name="Sizing" fields={[field("KELLY_FRACTION")]} dirtyCount={0}>
        <div>Field content</div>
      </TunableGroupCard>
    );
    expect(screen.queryByText(/modified/)).not.toBeInTheDocument();

    rerender(
      <TunableGroupCard name="Sizing" fields={[field("KELLY_FRACTION")]} dirtyCount={2}>
        <div>Field content</div>
      </TunableGroupCard>
    );
    expect(screen.getByText("2 modified")).toBeInTheDocument();
  });

  it("renders the rejectedCount badge only when > 0", () => {
    const { rerender } = render(
      <TunableGroupCard name="Sizing" fields={[field("KELLY_FRACTION")]} rejectedCount={0}>
        <div>Field content</div>
      </TunableGroupCard>
    );
    expect(screen.queryByText(/rejected/)).not.toBeInTheDocument();

    rerender(
      <TunableGroupCard name="Sizing" fields={[field("KELLY_FRACTION")]} rejectedCount={1}>
        <div>Field content</div>
      </TunableGroupCard>
    );
    expect(screen.getByText("1 rejected")).toBeInTheDocument();
  });

  it("a fields.length===0 group renders nothing", () => {
    const { container } = render(
      <TunableGroupCard name="Empty Group" fields={[]}>
        <div>Field content</div>
      </TunableGroupCard>
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("Field content")).not.toBeInTheDocument();
  });

  it("reduced-motion preference does not change the open/close logic", async () => {
    // matchMedia is stubbed globally in test-setup.ts to always report
    // matches:false; override it here for this one test only, then restore,
    // to prove useReducedMotion's branch doesn't affect toggle correctness.
    const original = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia;

    try {
      const user = userEvent.setup();
      render(
        <TunableGroupCard name="Sizing" fields={[field("KELLY_FRACTION")]}>
          <div>Field content</div>
        </TunableGroupCard>
      );

      await user.click(screen.getByTestId("group-header-sizing"));
      expect(await screen.findByText("Field content")).toBeInTheDocument();
    } finally {
      window.matchMedia = original;
    }
  });
});
