/**
 * AIChatInterface.test.tsx — the panel is only CSS-translated off-screen
 * when closed (isOpen=false), not unmounted (so the slide-in/out transition
 * keeps working). Covers the fix: it must not remain keyboard-focusable or
 * exposed to assistive tech while visually hidden.
 *
 * jsdom (vitest's DOM environment) does not implement the `inert` IDL
 * property reflection, so these assert on the raw `inert` HTML attribute
 * via getAttribute rather than the `.inert` DOM property.
 *
 * Also covers the optional `contextText` prop (fed by webapp/src/chat/
 * ChatContext.tsx's `openChat(contextText)` — see e.g. the Options Matrix
 * screen's "Ask Gemini" button) being threaded through to the outgoing
 * `/api/chat` request body as the new `context` field (see
 * api/data_api.py::ChatMessageRequest.context), byte-for-byte absent when
 * no context was supplied.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AIChatInterface from "./AIChatInterface";

/** Builds a fetch Response whose body streams the given SSE events (matching
 * the real `/api/chat` payload shape: `{type, content}` frames terminated by
 * `data: [DONE]`), all delivered in a single chunk. */
function makeSseResponse(events: Array<{ type: string; content: string }>): Response {
  const frames = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("") + "data: [DONE]\n\n";
  const bytes = new TextEncoder().encode(frames);
  let delivered = false;
  return {
    ok: true,
    body: {
      getReader() {
        return {
          read: async () => {
            if (delivered) return { done: true, value: undefined };
            delivered = true;
            return { done: false, value: bytes };
          },
        };
      },
    },
  } as unknown as Response;
}

async function sendMessage(text: string) {
  const textarea = screen.getByPlaceholderText("Ask a question about your portfolio...");
  fireEvent.change(textarea, { target: { value: text } });
  const form = textarea.closest("form");
  if (!form) throw new Error("form not found");
  fireEvent.submit(form);
}

function getPanel(container: HTMLElement): HTMLDivElement {
  const panel = container.querySelector('[data-testid="ai-chat-panel"]');
  if (!panel) throw new Error("panel not found");
  return panel as HTMLDivElement;
}

describe("AIChatInterface closed-panel a11y", () => {
  it("marks the panel inert and aria-hidden when closed", () => {
    const { container } = render(<AIChatInterface isOpen={false} onClose={() => {}} />);
    const panel = getPanel(container);
    expect(panel.getAttribute("inert")).not.toBeNull();
    expect(panel).toHaveAttribute("aria-hidden", "true");
  });

  it("is neither inert nor aria-hidden=true when open", () => {
    const { container } = render(<AIChatInterface isOpen={true} onClose={() => {}} />);
    const panel = getPanel(container);
    expect(panel.getAttribute("inert")).toBeNull();
    expect(panel).toHaveAttribute("aria-hidden", "false");
  });

  it("textarea sits inside the inert subtree while the panel is closed", () => {
    render(<AIChatInterface isOpen={false} onClose={() => {}} />);
    const textarea = screen.getByPlaceholderText("Ask a question about your portfolio...");
    const panel = textarea.closest('[data-testid="ai-chat-panel"]') as HTMLDivElement;
    expect(panel.getAttribute("inert")).not.toBeNull();
  });
});

describe("AIChatInterface context wiring", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("omits `context` from the request body when no contextText prop is supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeSseResponse([{ type: "MESSAGE", content: "hi there" }]));
    vi.stubGlobal("fetch", fetchMock);

    render(<AIChatInterface isOpen={true} onClose={() => {}} />);
    await sendMessage("hello");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.message).toBe("hello");
    expect(body).not.toHaveProperty("context");
  });

  it("threads a supplied contextText prop through as the `context` request field", async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeSseResponse([{ type: "MESSAGE", content: "hi there" }]));
    vi.stubGlobal("fetch", fetchMock);

    const contextText = "AAPL: strategy=Put Credit Spread, AltmanZ=3.10, daysToEarnings=5, earningsRisk=yes";
    render(<AIChatInterface isOpen={true} onClose={() => {}} contextText={contextText} />);
    await sendMessage("which of these have earnings risk?");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    // The user's literal question stays clean in the outgoing message --
    // context travels as its own field, not prepended into the text.
    expect(body.message).toBe("which of these have earnings risk?");
    expect(body.context).toBe(contextText);
  });
});

describe("AIChatInterface Live Mode controls", () => {
  it("toggles between text chat and live voice mode", () => {
    render(<AIChatInterface isOpen={true} onClose={() => {}} />);
    const toggleBtn = screen.getByTitle("Switch to Live Voice Chat");
    expect(toggleBtn).toBeInTheDocument();

    // Toggle to Live Voice
    fireEvent.click(toggleBtn);
    expect(screen.getByText("Voice Live")).toBeInTheDocument();
    expect(screen.getByText("Live Audio")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Type or speak to Gemini Live...")).toBeInTheDocument();
    expect(screen.getByTitle("Speak to Gemini Live")).toBeInTheDocument();

    // Toggle back to Text Chat
    fireEvent.click(screen.getByTitle("Switch to Text Chat"));
    expect(screen.getByText("Text Chat")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Ask a question about your portfolio...")).toBeInTheDocument();
  });
});

describe("AIChatInterface model selector", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the model selection dropdown bar in text chat mode", () => {
    render(<AIChatInterface isOpen={true} onClose={() => {}} />);
    const bar = screen.getByTestId("ai-model-selector-bar");
    expect(bar).toBeInTheDocument();
    expect(screen.getByLabelText("AI Model Selector")).toBeInTheDocument();
  });

  it("threads chosen model and provider into request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeSseResponse([{ type: "MESSAGE", content: "response" }]));
    vi.stubGlobal("fetch", fetchMock);

    render(<AIChatInterface isOpen={true} onClose={() => {}} />);

    const select = screen.getByLabelText("AI Model Selector");
    fireEvent.change(select, { target: { value: "anthropic:claude-3-5-sonnet-20241022" } });

    await sendMessage("Analyze risk");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.provider).toBe("anthropic");
    expect(body.model).toBe("claude-3-5-sonnet-20241022");
  });

  it("allows switching to custom model input", async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeSseResponse([{ type: "MESSAGE", content: "response" }]));
    vi.stubGlobal("fetch", fetchMock);

    render(<AIChatInterface isOpen={true} onClose={() => {}} />);

    const select = screen.getByLabelText("AI Model Selector");
    fireEvent.change(select, { target: { value: "custom" } });

    const input = screen.getByPlaceholderText("e.g. deepseek-ai/DeepSeek-V3");
    expect(input).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "meta-llama/Llama-3-70b-chat" } });

    await sendMessage("Summarize macro");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.model).toBe("meta-llama/Llama-3-70b-chat");
  });
});
