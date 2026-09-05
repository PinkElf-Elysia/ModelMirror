import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Model } from "../data/models";
import AudioCreationWorkspace from "./AudioCreationWorkspace";

const model = {
  id: "google/lyria-3-clip-preview",
  name: "Lyria Test",
} as Model;

function jsonResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  } as Response;
}

function catalogResponse() {
  return {
    status: "online",
    stale: false,
    profiles: [
      {
        model_id: model.id,
        display_name: model.name,
        invocable: true,
        interaction_status: "ready",
        status_reason: null,
        operations: ["generate_audio"],
        output_formats: ["mp3"],
        supports_image_prompt: false,
        price_per_generation_usd: null,
        fixed_duration_seconds: 30,
      },
    ],
  };
}

function managedUncertainJob() {
  return {
    job_id: "audiojob-managed-1",
    status: "failed",
    requested_model: model.id,
    actual_model: null,
    provider: "openrouter",
    generation_id: "upstream-generation-secret",
    parameters: { has_image: false },
    usage: { cost_usd: null, cost_kind: "unavailable" },
    output_bytes: 0,
    created_at: "2026-08-31T12:00:00Z",
    updated_at: "2026-08-31T12:00:01Z",
    expires_at: null,
    error: {
      code: "provider_result_uncertain",
      message: "Provider 结果尚未确认。",
    },
    execution_mode: "managed",
    provider_dispatch_state: "uncertain",
    retry_allowed: false,
    fallback_reason_codes: ["provider_result_uncertain"],
    provider_route_receipts: [
      {
        contract_version: "modelmirror-provider-workload-routing-v1",
        entry_id: "audio_generation",
        routing_mode: "managed_required",
        run_reference: "workrun-audio-generation-1",
        status: "uncertain",
        call_count: 1,
        reason_codes: ["provider_result_uncertain"],
        calls: [
          {
            call_sequence: 1,
            model_id: model.id,
            actual_model: null,
            dispatched: true,
            status: "uncertain",
            error_code: "provider_result_uncertain",
          },
        ],
        base_url: "https://must-not-render.example/v1",
        api_key: "must-not-render-secret",
      },
    ],
  };
}

describe("AudioCreationWorkspace managed audio generation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("sends one consistent idempotency key and renders sanitized uncertain evidence without replay controls", async () => {
    vi.spyOn(window.crypto, "randomUUID").mockReturnValue(
      "01234567-89ab-4def-8123-456789abcdef",
    );
    let submittedHeader = "";
    let submittedFormKey = "";
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/multimodal/audio/models") {
          return jsonResponse(catalogResponse());
        }
        if (url === "/api/multimodal/audio/jobs" && init?.method === "POST") {
          submittedHeader = new Headers(init.headers).get("Idempotency-Key") ?? "";
          submittedFormKey =
            init.body instanceof FormData
              ? String(init.body.get("idempotency_key") ?? "")
              : "";
          return jsonResponse(managedUncertainJob());
        }
        if (url === "/api/multimodal/audio/jobs") {
          return jsonResponse({ jobs: [] });
        }
        throw new Error(`Unexpected fetch: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <AudioCreationWorkspace model={model} />
      </MemoryRouter>,
    );

    fireEvent.change(await screen.findByPlaceholderText(/轻快的城市流行乐/), {
      target: { value: "一段短小的合成音乐" },
    });
    fireEvent.click(screen.getByRole("button", { name: /提交生成/ }));

    await waitFor(() => {
      expect(submittedHeader).toBe(
        "audio-01234567-89ab-4def-8123-456789abcdef",
      );
    });
    expect(submittedFormKey).toBe(submittedHeader);
    expect(
      await screen.findByText("音乐生成控制面：已纳管 · 1 次 Provider 调用"),
    ).toBeVisible();
    expect(
      screen.getByText(/系统不会自动重放同一任务/),
    ).toHaveTextContent("provider_result_uncertain");
    expect(
      screen.getByText(/Managed 任务会保留脱敏幂等与审计记录/),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "移除记录" }),
    ).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain(
      "upstream-generation-secret",
    );
    expect(document.body.textContent).not.toContain("must-not-render");
  });

  it("reuses the key for an unchanged active task and rotates it only after form change or a safe terminal", async () => {
    const randomUUID = vi
      .spyOn(window.crypto, "randomUUID")
      .mockReturnValueOnce("11111111-1111-4111-8111-111111111111")
      .mockReturnValueOnce("22222222-2222-4222-8222-222222222222")
      .mockReturnValueOnce("33333333-3333-4333-8333-333333333333")
      .mockReturnValueOnce("44444444-4444-4444-8444-444444444444");
    const submittedKeys: string[] = [];
    let postCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/multimodal/audio/models") {
          return jsonResponse(catalogResponse());
        }
        if (url === "/api/multimodal/audio/jobs" && init?.method === "POST") {
          postCount += 1;
          submittedKeys.push(
            new Headers(init.headers).get("Idempotency-Key") ?? "",
          );
          const terminal = postCount >= 4;
          return jsonResponse({
            ...managedUncertainJob(),
            job_id: terminal
              ? `audiojob-managed-succeeded-${postCount}`
              : "audiojob-managed-active-1",
            status: terminal ? "succeeded" : "queued",
            actual_model: terminal ? model.id : null,
            output_bytes: terminal ? 1024 : 0,
            error: null,
            provider_dispatch_state: terminal ? "confirmed" : "dispatched",
            retry_allowed: true,
            fallback_reason_codes: [],
          });
        }
        if (url === "/api/multimodal/audio/jobs") {
          return jsonResponse({ jobs: [] });
        }
        throw new Error(`Unexpected fetch: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <AudioCreationWorkspace model={model} />
      </MemoryRouter>,
    );

    const prompt = await screen.findByPlaceholderText(/轻快的城市流行乐/);
    fireEvent.change(prompt, { target: { value: "保持不变的音乐描述" } });
    const submit = screen.getByRole("button", { name: /提交生成/ });

    fireEvent.click(submit);
    await waitFor(() => expect(postCount).toBe(1));
    fireEvent.click(submit);
    await waitFor(() => expect(postCount).toBe(2));
    expect(submittedKeys[1]).toBe(submittedKeys[0]);
    expect(randomUUID).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "开始新任务" }));
    fireEvent.click(submit);
    await waitFor(() => expect(postCount).toBe(3));
    expect(submittedKeys[2]).not.toBe(submittedKeys[1]);

    fireEvent.change(prompt, { target: { value: "已经修改的音乐描述" } });
    fireEvent.click(submit);
    await waitFor(() => expect(postCount).toBe(4));
    expect(submittedKeys[3]).not.toBe(submittedKeys[2]);

    fireEvent.click(submit);
    await waitFor(() => expect(postCount).toBe(5));
    expect(submittedKeys[4]).not.toBe(submittedKeys[3]);
    expect(randomUUID).toHaveBeenCalledTimes(4);
  });

  it("keeps legacy task removal compatible when the additive control fields are absent", async () => {
    const legacyJob = {
      ...managedUncertainJob(),
      job_id: "audiojob-legacy-1",
      generation_id: "legacy-generation-id",
    };
    delete (legacyJob as Partial<typeof legacyJob>).execution_mode;
    delete (legacyJob as Partial<typeof legacyJob>).provider_route_receipts;
    delete (legacyJob as Partial<typeof legacyJob>).provider_dispatch_state;
    delete (legacyJob as Partial<typeof legacyJob>).retry_allowed;
    delete (legacyJob as Partial<typeof legacyJob>).fallback_reason_codes;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/multimodal/audio/models") {
          return jsonResponse(catalogResponse());
        }
        if (url === "/api/multimodal/audio/jobs") {
          return jsonResponse({ jobs: [legacyJob] });
        }
        throw new Error(`Unexpected fetch: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <AudioCreationWorkspace model={model} />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("button", { name: "移除记录" }),
    ).toBeVisible();
    expect(
      screen.queryByText(/Managed 任务会保留脱敏幂等与审计记录/),
    ).not.toBeInTheDocument();
  });
});
