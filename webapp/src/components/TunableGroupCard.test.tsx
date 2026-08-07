/**
 * TunableGroupCard.test.tsx — the per-group wrapper every
 * settings screen (via GenericSettingsEditor) renders one of per tunable
 * group. Covers the rendering logic (the dirty/rejected badges, the fields.length===0 early return).
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
  it("renders the content immediately", async () => {
    render(
      <TunableGroupCard name="Sizing" fields={[field("KELLY_FRACTION")]}>
        <div>Field content</div>
      </TunableGroupCard>
    );

    expect(screen.getByText("Field content")).toBeInTheDocument();
    expect(screen.getByTestId("group-header-sizing")).toHaveTextContent("Sizing");
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
});
