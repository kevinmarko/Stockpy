import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CreateDataApp } from "./CreateDataApp";
import { MemoryRouter } from "react-router";

vi.mock("../components/TabGuide", () => ({
  TabGuide: () => <div data-testid="tab-guide" />
}));

describe("CreateDataApp screen", () => {
  it("renders correctly", () => {
    render(
      <MemoryRouter>
        <CreateDataApp />
      </MemoryRouter>
    );
    expect(screen.getByText("Configuration")).toBeInTheDocument();
    expect(screen.getByLabelText("App Name")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create App" })).toBeInTheDocument();
  });

  it("handles mock submission", async () => {
    render(
      <MemoryRouter>
        <CreateDataApp />
      </MemoryRouter>
    );
    
    const user = userEvent.setup();

    const nameInput = screen.getByLabelText("App Name");
    await user.type(nameInput, "My Dashboard");

    const submitBtn = screen.getByRole("button", { name: "Create App" });
    await user.click(submitBtn);

    // Should show pending state
    expect(submitBtn).toBeDisabled();

    // Should show success state eventually (after 500ms mock delay)
    expect(await screen.findByText("Data App Created")).toBeInTheDocument();
    expect(screen.getByText("Your application has been provisioned.")).toBeInTheDocument();
  });
});

