import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { models } from "../data/models";
import OpenRouterBatchWorkspace from "./OpenRouterBatchWorkspace";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("OpenRouterBatchWorkspace", () => {
  it("reuses a pending idempotency key after a network failure", async () => {
    const model = models.find((item) =>
      item.serving_variants.some((variant) => variant.type === "batch"),
    );
    const variant = model?.serving_variants.find((item) => item.type === "batch");
    expect(model).toBeDefined();
    expect(variant).toBeDefined();
    vi.spyOn(window.crypto, "randomUUID").mockReturnValue(
      "01234567-89ab-4def-8123-456789abcdef",
    );
    const requests: RequestInit[] = [];
    vi.spyOn(globalThis, "fetch")
      .mockImplementationOnce(async (_input, init) => {
        requests.push(init ?? {});
        throw new TypeError("network unavailable");
      })
      .mockImplementationOnce(async (_input, init) => {
        requests.push(init ?? {});
        return new Response(
          JSON.stringify({
            id: "mmbatch_0123456789abcdef0123456789abcdef",
            object: "batch",
            endpoint: variant!.endpoint,
            model: variant!.request_model_id,
            completion_window: "24h",
            status: "validating",
            created_at: 1,
            finalized_at: null,
            request_counts: { total: 1, completed: 0, failed: 0 },
            usage: null,
            results: null,
            error: null,
            billing_authoritative: false,
          }),
          { status: 202, headers: { "Content-Type": "application/json" } },
        );
      });

    render(
      <MemoryRouter>
        <OpenRouterBatchWorkspace model={model!} variant={variant!} />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getAllByRole("textbox")[0], {
      target: { value: "one managed batch request" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交批处理任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "network unavailable",
    );
    expect(
      window.localStorage.getItem(
        `modelmirror-openrouter-batch-pending:${model!.id}`,
      ),
    ).toBe("01234567-89ab-4def-8123-456789abcdef");
    expect(
      screen.getByText(
        "存在一项待确认提交。再次提交会复用原请求标识，便于 Managed 模式识别重复提交。",
      ),
    ).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "提交批处理任务" }));
    expect(
      await screen.findByText("mmbatch_0123456789abcdef0123456789abcdef"),
    ).toBeVisible();
    await waitFor(() => expect(requests).toHaveLength(2));
    const firstHeaders = new Headers(requests[0].headers);
    const secondHeaders = new Headers(requests[1].headers);
    expect(firstHeaders.get("Idempotency-Key")).toBe(
      "01234567-89ab-4def-8123-456789abcdef",
    );
    expect(secondHeaders.get("Idempotency-Key")).toBe(
      firstHeaders.get("Idempotency-Key"),
    );
    expect(
      window.localStorage.getItem(
        `modelmirror-openrouter-batch:${model!.id}`,
      ),
    ).toBe("mmbatch_0123456789abcdef0123456789abcdef");
    expect(
      window.localStorage.getItem(
        `modelmirror-openrouter-batch-pending:${model!.id}`,
      ),
    ).toBeNull();
  });

  it("keeps polling after a transient refresh failure and clears the stale error", async () => {
    vi.useFakeTimers();
    const model = models.find((item) =>
      item.serving_variants.some((variant) => variant.type === "batch"),
    );
    const variant = model?.serving_variants.find((item) => item.type === "batch");
    expect(model).toBeDefined();
    expect(variant).toBeDefined();
    const batchId = "mmbatch_abcdef0123456789abcdef0123456789";
    const inProgress = {
      id: batchId,
      object: "batch",
      endpoint: variant!.endpoint,
      model: variant!.request_model_id,
      completion_window: "24h",
      status: "in_progress",
      created_at: 1,
      finalized_at: null,
      request_counts: { total: 1, completed: 0, failed: 0 },
      usage: null,
      results: null,
      error: null,
      billing_authoritative: false,
    };
    window.localStorage.setItem(
      `modelmirror-openrouter-batch:${model!.id}`,
      batchId,
    );
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(inProgress), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockRejectedValueOnce(new Error("无法刷新 OpenRouter Batch 状态，请稍后重试。"))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...inProgress,
            status: "completed",
            finalized_at: 2,
            request_counts: { total: 1, completed: 1, failed: 0 },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );

    render(
      <MemoryRouter>
        <OpenRouterBatchWorkspace model={model!} variant={variant!} />
      </MemoryRouter>,
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText(batchId)).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "无法刷新 OpenRouter Batch 状态，请稍后重试。",
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText("已完成")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledTimes(3);
  });

  it("retries a saved Batch restore after the preview server returns", async () => {
    vi.useFakeTimers();
    const model = models.find((item) =>
      item.serving_variants.some((variant) => variant.type === "batch"),
    );
    const variant = model?.serving_variants.find((item) => item.type === "batch");
    expect(model).toBeDefined();
    expect(variant).toBeDefined();
    const batchId = "mmbatch_1234567890abcdef1234567890abcdef";
    window.localStorage.setItem(
      `modelmirror-openrouter-batch:${model!.id}`,
      batchId,
    );
    vi.spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("preview unavailable"))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: batchId,
            object: "batch",
            endpoint: variant!.endpoint,
            model: variant!.request_model_id,
            completion_window: "24h",
            status: "completed",
            created_at: 1,
            finalized_at: 2,
            request_counts: { total: 1, completed: 1, failed: 0 },
            usage: null,
            results: null,
            error: null,
            billing_authoritative: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );

    render(
      <MemoryRouter>
        <OpenRouterBatchWorkspace model={model!} variant={variant!} />
      </MemoryRouter>,
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      `暂时无法恢复已保存的批处理任务 ${batchId}`,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText(batchId)).toBeVisible();
    expect(screen.getByText("已完成")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  });
});
