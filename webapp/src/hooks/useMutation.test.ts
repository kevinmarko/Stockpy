import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useMutation } from "./useMutation";

describe("useMutation", () => {
  it("initial state is idle", () => {
    const { result } = renderHook(() => useMutation(async () => "ok"));
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.result).toBeNull();
  });

  it("run() sets pending during the call, then result on success", async () => {
    let resolveFn: (v: string) => void;
    const fn = vi.fn(
      () =>
        new Promise<string>((res) => {
          resolveFn = res;
        })
    );
    const { result } = renderHook(() => useMutation(fn));

    let runPromise: Promise<string | undefined>;
    act(() => {
      runPromise = result.current.run();
    });
    expect(result.current.pending).toBe(true);
    expect(result.current.error).toBeNull();

    await act(async () => {
      resolveFn!("done");
      await runPromise;
    });

    expect(result.current.pending).toBe(false);
    expect(result.current.result).toBe("done");
    expect(result.current.error).toBeNull();
  });

  it("run() sets error on rejection (instanceof Error narrowing) and clears pending", async () => {
    const fn = vi.fn(async () => {
      throw new Error("boom");
    });
    const { result } = renderHook(() => useMutation(fn));

    await act(async () => {
      await result.current.run();
    });

    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBe("boom");
    expect(result.current.result).toBeNull();
  });

  it("a non-Error throw still produces a string error (never crashes)", async () => {
    const fn = vi.fn(async () => {
      // eslint-disable-next-line @typescript-eslint/no-throw-literal
      throw "not an Error instance";
    });
    const { result } = renderHook(() => useMutation(fn));

    await act(async () => {
      await result.current.run();
    });
    expect(result.current.error).toBe("Request failed");
  });

  it("run() clears a previous error on a new attempt", async () => {
    let shouldFail = true;
    const fn = vi.fn(async () => {
      if (shouldFail) throw new Error("first failure");
      return "second try ok";
    });
    const { result } = renderHook(() => useMutation(fn));

    await act(async () => {
      await result.current.run();
    });
    expect(result.current.error).toBe("first failure");

    shouldFail = false;
    await act(async () => {
      await result.current.run();
    });
    expect(result.current.error).toBeNull();
    expect(result.current.result).toBe("second try ok");
  });

  it("reset() clears pending/error/result back to idle", async () => {
    const fn = vi.fn(async () => {
      throw new Error("boom");
    });
    const { result } = renderHook(() => useMutation(fn));

    await act(async () => {
      await result.current.run();
    });
    expect(result.current.error).toBe("boom");

    act(() => {
      result.current.reset();
    });
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.result).toBeNull();
  });

  it("does not update state after unmount (no React act warning / no crash)", async () => {
    let resolveFn: (v: string) => void;
    const fn = vi.fn(
      () =>
        new Promise<string>((res) => {
          resolveFn = res;
        })
    );
    const { result, unmount } = renderHook(() => useMutation(fn));

    let runPromise: Promise<string | undefined>;
    act(() => {
      runPromise = result.current.run();
    });
    unmount();

    await act(async () => {
      resolveFn!("late arrival");
      await runPromise;
    });
    // No assertion beyond "this didn't throw" -- the alive-ref guard is what
    // prevents a setState-after-unmount warning/crash here.
  });

  it("run() passes arguments through and returns the resolved value", async () => {
    const fn = vi.fn(async (a: number, b: number) => a + b);
    const { result } = renderHook(() => useMutation(fn));

    let ret: number | undefined;
    await act(async () => {
      ret = await result.current.run(2, 3);
    });
    expect(fn).toHaveBeenCalledWith(2, 3);
    expect(ret).toBe(5);
    await waitFor(() => expect(result.current.result).toBe(5));
  });
});
