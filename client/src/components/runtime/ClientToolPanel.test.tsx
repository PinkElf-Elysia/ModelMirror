import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ClientToolPanel, { type ClientToolRequest } from "./ClientToolPanel";

function request(
  requestId: string,
  taskId: string,
  status: string,
): ClientToolRequest {
  return {
    request_id: requestId,
    host_id: "host-a",
    task_id: taskId,
    scope_type: "conversation",
    scope_id: "xpert-a:conversation-a",
    tool_name: "browser_click",
    arguments: {},
    mutating: true,
    status,
    result_length: 0,
    updated_at: 1,
  };
}

function response(requests: ClientToolRequest[]) {
  return Promise.resolve(new Response(JSON.stringify({ requests }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ClientToolPanel terminal notifications", () => {
  it("keeps the legacy callback once per refresh and reports every resolved request", async () => {
    const pending = [
      request("request-a", "task-a", "pending"),
      request("request-b", "task-b", "running"),
    ];
    const terminal = [
      request("request-a", "task-a", "completed"),
      request("request-b", "task-b", "failed"),
    ];
    let current = pending;
    const fetchMock = vi.fn(() => response(current));
    const callbackBarrier = deferred();
    const onResolved = vi.fn(() => callbackBarrier.promise);
    const onResolvedRequest = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(
      <ClientToolPanel
        onResolved={onResolved}
        onResolvedRequest={onResolvedRequest}
        scopeId="xpert-a:conversation-a"
        scopeType="conversation"
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(onResolved).not.toHaveBeenCalled();
    expect(onResolvedRequest).not.toHaveBeenCalled();

    current = terminal;
    fireEvent.click(screen.getByRole("button", { name: /客户端工具/ }));
    fireEvent.click(screen.getByRole("button", { name: "刷新客户端请求" }));

    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
    // A callback-triggered parent rerender can replace the callback identity
    // and therefore run the refresh effect again while the first callback is
    // still pending. Claimed terminal requests must not be delivered twice.
    const replacementCallback = vi.fn();
    rerender(
      <ClientToolPanel
        onResolved={replacementCallback}
        onResolvedRequest={onResolvedRequest}
        scopeId="xpert-a:conversation-a"
        scopeType="conversation"
      />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(replacementCallback).not.toHaveBeenCalled();
    await act(async () => {
      callbackBarrier.resolve();
      await callbackBarrier.promise;
    });

    await waitFor(() => expect(onResolvedRequest).toHaveBeenCalledTimes(2));
    expect(onResolved).toHaveBeenCalledTimes(1);
    expect(onResolvedRequest.mock.calls.map(([item]) => [item.request_id, item.task_id])).toEqual([
      ["request-a", "task-a"],
      ["request-b", "task-b"],
    ]);
  });

  it("does not announce terminal requests from the initial snapshot", async () => {
    const onResolved = vi.fn();
    const onResolvedRequest = vi.fn();
    vi.stubGlobal("fetch", vi.fn(() => response([
      request("request-initial", "task-initial", "completed"),
    ])));

    render(
      <ClientToolPanel
        onResolved={onResolved}
        onResolvedRequest={onResolvedRequest}
        taskId="task-initial"
      />,
    );

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(onResolved).not.toHaveBeenCalled();
    expect(onResolvedRequest).not.toHaveBeenCalled();
  });

  it("does not redeliver a claimed terminal batch when the legacy callback rejects", async () => {
    let current = [request("request-reject", "task-reject", "pending")];
    const fetchMock = vi.fn(() => response(current));
    const onResolved = vi.fn(() => Promise.reject(new Error("resume rejected")));
    const onResolvedRequest = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ClientToolPanel
        onResolved={onResolved}
        onResolvedRequest={onResolvedRequest}
        taskId="task-reject"
      />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    current = [request("request-reject", "task-reject", "failed")];
    fireEvent.click(screen.getByRole("button", { name: /客户端工具/ }));
    fireEvent.click(screen.getByRole("button", { name: "刷新客户端请求" }));
    expect(await screen.findByText("resume rejected")).toBeVisible();
    expect(onResolved).toHaveBeenCalledTimes(1);
    expect(onResolvedRequest).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "刷新客户端请求" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(onResolved).toHaveBeenCalledTimes(1);
    expect(onResolvedRequest).not.toHaveBeenCalled();
  });
});
