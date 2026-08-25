import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { usePolling } from "./usePolling";

function Probe({ loader }: { loader: (signal: AbortSignal) => Promise<number> }) {
  const state = usePolling(loader, 1_000);
  return <output>{state.data ?? "empty"}</output>;
}

function TerminalProbe({ loader }: { loader: (signal: AbortSignal) => Promise<number> }) {
  const state = usePolling(loader, 1_000, true, () => false);
  return <output>{state.data ?? "empty"}</output>;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});

test("polling pauses while hidden and resumes immediately when visible", async () => {
  vi.useFakeTimers();
  const loader = vi.fn(async () => loader.mock.calls.length);
  render(<Probe loader={loader} />);
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(loader).toHaveBeenCalledTimes(1);
  expect(screen.getByText("1")).toBeInTheDocument();

  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
  await act(() => vi.advanceTimersByTimeAsync(5_000));
  expect(loader).toHaveBeenCalledTimes(1);

  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  document.dispatchEvent(new Event("visibilitychange"));
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(loader).toHaveBeenCalledTimes(2);
});

test("polling aborts an in-flight request during cleanup", async () => {
  vi.useFakeTimers();
  let observedSignal: AbortSignal | undefined;
  const loader = vi.fn((signal: AbortSignal) => {
    observedSignal = signal;
    return new Promise<number>(() => undefined);
  });
  const view = render(<Probe loader={loader} />);
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(observedSignal?.aborted).toBe(false);
  view.unmount();
  expect(observedSignal?.aborted).toBe(true);
});

test("polling stops scheduling after a terminal value", async () => {
  vi.useFakeTimers();
  const loader = vi.fn(async () => 1);
  render(<TerminalProbe loader={loader} />);
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(screen.getByText("1")).toBeInTheDocument();
  await act(() => vi.advanceTimersByTimeAsync(5_000));
  expect(loader).toHaveBeenCalledTimes(1);
});
