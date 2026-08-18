/**
 * AIChatInterface.liveTranscript.test.tsx
 * ========================================
 * Regression coverage for two fixes to the Gemini Live voice-mode wiring in
 * AIChatInterface.tsx, found during review of PR #768:
 *
 * 1. **Transcript accumulation.** input_transcription/output_transcription
 *    WS events stream INCREMENTAL fragments (mirroring how Gemini's own
 *    audio-transcription samples print each chunk with no separator --
 *    the caller is expected to concatenate them to reconstruct the full
 *    utterance), not the full cumulative text restated each time. The
 *    original code REPLACED the live message's content on every event,
 *    which would have silently dropped everything transcribed earlier in
 *    a multi-fragment turn. onUserTranscript/onModelTranscript now append;
 *    onTurnComplete seals the trailing live message (`turnComplete: true`)
 *    so the NEXT turn's fragments open a fresh bubble instead of
 *    continuing to append into the finished one.
 *
 * 2. **No reconnect on contextText change while live.** The connect/
 *    disconnect effect used to list `contextText` as a dependency, so any
 *    change to that prop while a live voice session was open tore down and
 *    reconnected the WebSocket mid-conversation (connectLive() calls
 *    disconnectLive() first). It's now only re-run when isOpen/isLiveMode
 *    change; a separate effect pushes context UPDATES over the
 *    already-open connection via sendContext instead.
 *
 * This test mocks `useGeminiLive` directly (unlike AIChatInterface.test.tsx,
 * which exercises the real hook's own mock-mode branch) so it can invoke
 * the exact onUserTranscript/onModelTranscript/onTurnComplete/onThought
 * callbacks AIChatInterface.tsx wires up, independent of the real
 * WebSocket/audio plumbing, and can assert on connectLive/sendContext call
 * counts directly.
 */
import { act } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AIChatInterface from "./AIChatInterface";

type CapturedOptions = {
  onUserTranscript?: (text: string) => void;
  onModelTranscript?: (text: string, isPartial?: boolean) => void;
  onTurnComplete?: () => void;
  onThought?: (thought: string) => void;
  onError?: (error: string) => void;
};

let capturedOptions: CapturedOptions | null = null;
const connectLiveMock = vi.fn();
const disconnectLiveMock = vi.fn();
const sendContextMock = vi.fn();

vi.mock("../chat/useGeminiLive", () => ({
  useGeminiLive: (options: CapturedOptions) => {
    capturedOptions = options;
    return {
      status: "connected" as const,
      isMicActive: false,
      isSpeaking: false,
      liveModel: "gemini-3.1-flash-live-preview",
      liveVoice: "Aoede",
      errorMessage: null,
      connectLive: connectLiveMock,
      disconnectLive: disconnectLiveMock,
      startMic: vi.fn(),
      stopMic: vi.fn(),
      toggleMic: vi.fn(),
      sendTextMessage: vi.fn(),
      sendContext: sendContextMock,
      interruptPlayback: vi.fn(),
    };
  },
}));

function fireLive(fn: (opts: CapturedOptions) => void) {
  if (!capturedOptions) throw new Error("useGeminiLive options not captured yet");
  act(() => fn(capturedOptions as CapturedOptions));
}

beforeEach(() => {
  capturedOptions = null;
  connectLiveMock.mockClear();
  disconnectLiveMock.mockClear();
  sendContextMock.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("AIChatInterface live transcript accumulation", () => {
  it("accumulates multiple output_transcription fragments into one message instead of overwriting", () => {
    render(<AIChatInterface isOpen={true} onClose={() => {}} />);

    fireLive((o) => o.onModelTranscript?.("Hello", false));
    fireLive((o) => o.onModelTranscript?.(" there", false));
    fireLive((o) => o.onModelTranscript?.(", how can I help?", false));

    expect(screen.getByText("Hello there, how can I help?")).toBeInTheDocument();
  });

  it("accumulates multiple input_transcription fragments for the user's own speech", () => {
    render(<AIChatInterface isOpen={true} onClose={() => {}} />);

    fireLive((o) => o.onUserTranscript?.("What's my"));
    fireLive((o) => o.onUserTranscript?.(" portfolio"));
    fireLive((o) => o.onUserTranscript?.(" value?"));

    expect(screen.getByText("What's my portfolio value?")).toBeInTheDocument();
  });

  it("still space-joins streamed part.text (isPartial=true) fragments as before", () => {
    render(<AIChatInterface isOpen={true} onClose={() => {}} />);

    fireLive((o) => o.onModelTranscript?.("Portfolio", true));
    fireLive((o) => o.onModelTranscript?.("is up", true));
    fireLive((o) => o.onModelTranscript?.("1.2%.", true));

    expect(screen.getByText("Portfolio is up 1.2%.")).toBeInTheDocument();
  });

  it("seals the message on turn_complete so the next turn's transcript opens a new bubble", () => {
    render(<AIChatInterface isOpen={true} onClose={() => {}} />);

    fireLive((o) => o.onModelTranscript?.("First reply.", false));
    fireLive((o) => o.onTurnComplete?.());
    fireLive((o) => o.onModelTranscript?.("Second reply.", false));

    expect(screen.getByText("First reply.")).toBeInTheDocument();
    expect(screen.getByText("Second reply.")).toBeInTheDocument();
  });

  it("does not keep appending thoughts into an already-sealed turn's message", () => {
    render(<AIChatInterface isOpen={true} onClose={() => {}} />);

    fireLive((o) => o.onThought?.("Querying get_platform_status..."));
    fireLive((o) => o.onModelTranscript?.("Everything looks fine.", false));
    fireLive((o) => o.onTurnComplete?.());
    fireLive((o) => o.onThought?.("Querying get_current_portfolio..."));
    fireLive((o) => o.onModelTranscript?.("Your portfolio is up today.", false));

    expect(screen.getByText("Everything looks fine.")).toBeInTheDocument();
    expect(screen.getByText("Your portfolio is up today.")).toBeInTheDocument();
  });
});

describe("AIChatInterface live contextText updates", () => {
  it("connects exactly once on entering Live Mode and never reconnects when contextText changes afterward", () => {
    const { rerender } = render(
      <AIChatInterface isOpen={true} onClose={() => {}} contextText="AAPL context v1" />
    );

    fireEvent.click(screen.getByTitle("Switch to Live Voice Chat"));
    expect(connectLiveMock).toHaveBeenCalledTimes(1);
    expect(connectLiveMock).toHaveBeenCalledWith("AAPL context v1");

    connectLiveMock.mockClear();
    disconnectLiveMock.mockClear();

    rerender(
      <AIChatInterface isOpen={true} onClose={() => {}} contextText="AAPL context v2" />
    );
    rerender(
      <AIChatInterface isOpen={true} onClose={() => {}} contextText="AAPL context v3" />
    );

    // contextText changed twice while live; the session must NOT have been
    // torn down and reconnected for either change.
    expect(connectLiveMock).not.toHaveBeenCalled();
    expect(disconnectLiveMock).not.toHaveBeenCalled();
  });

  it("pushes contextText updates over the open connection via sendContext instead", () => {
    const { rerender } = render(
      <AIChatInterface isOpen={true} onClose={() => {}} contextText="AAPL context v1" />
    );

    fireEvent.click(screen.getByTitle("Switch to Live Voice Chat"));
    // connectLive already carried the initial context -- no redundant
    // sendContext for the same value on first connect.
    expect(sendContextMock).not.toHaveBeenCalled();

    rerender(
      <AIChatInterface isOpen={true} onClose={() => {}} contextText="AAPL context v2" />
    );

    expect(sendContextMock).toHaveBeenCalledWith("AAPL context v2");
  });
});
