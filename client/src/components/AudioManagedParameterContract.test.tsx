import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Model } from "../data/models";
import SpeechWorkspace from "./SpeechWorkspace";
import TranscriptionWorkspace, {
  estimateTranscriptionCostUsd,
} from "./TranscriptionWorkspace";

const model = {
  id: "openai/audio-test",
  name: "Audio Test",
} as Model;

const hourlyTranscriptionModel = {
  ...model,
  id: "microsoft/mai-transcribe-2",
  name: "Microsoft: MAI-Transcribe 2",
  media_pricing: {
    unit: "audio_hour",
    usd: 0.1,
  },
  note: "目录价为 $0.10/音频小时，真实短音频仍待人工验收。",
} as Model;

function providerRouteReceipt(
  entryId: "multimodal_transcription" | "multimodal_speech",
  status: "passed" | "failed" = "passed",
) {
  return {
    contract_version: "modelmirror-provider-workload-routing-v1",
    entry_id: entryId,
    routing_mode: "managed_required",
    run_reference: `workrun-${entryId}-${status}`,
    status,
    call_count: 1,
    reason_codes:
      status === "failed" ? ["provider_workload_upstream_failed"] : [],
    calls: [
      {
        call_sequence: 1,
        model_id: model.id,
        actual_model: model.id,
        dispatched: true,
        status,
        error_code:
          status === "failed" ? "provider_workload_upstream_failed" : null,
      },
    ],
    base_url: "https://must-not-render.example/v1",
    api_key: "must-not-render-secret",
  };
}

function jsonResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  } as Response;
}

function renderWithRouter(ui: React.ReactNode) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

function stubObjectUrls() {
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:audio-test");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
}

function stubTranscriptionRequest(status: number, payload: unknown) {
  class MockXMLHttpRequest {
    status = 0;
    response: unknown = null;
    responseText = "";
    responseType = "";
    upload = {
      onprogress: null as ((event: ProgressEvent) => void) | null,
      onload: null as ((event: ProgressEvent) => void) | null,
    };
    onload: ((event: ProgressEvent) => void) | null = null;
    onerror: ((event: ProgressEvent) => void) | null = null;
    onabort: ((event: ProgressEvent) => void) | null = null;

    open() {}

    setRequestHeader() {}

    send() {
      this.upload.onload?.(new ProgressEvent("load"));
      queueMicrotask(() => {
        this.status = status;
        this.response = payload;
        this.responseText = JSON.stringify(payload);
        this.onload?.(new ProgressEvent("load"));
      });
    }

    abort() {
      this.onabort?.(new ProgressEvent("abort"));
    }
  }

  vi.stubGlobal(
    "XMLHttpRequest",
    MockXMLHttpRequest as unknown as typeof XMLHttpRequest,
  );
}

describe("Managed audio parameter contract", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("restricts Dedicated STT selection to certified WAV input", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        feature_enabled: true,
        status: "managed_required",
        available: true,
        reason_code: "provider_workload_available",
        certified_input_formats: ["wav"],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const modelWithContractNote = {
      ...model,
      note: "目录价为 $0.10/音频小时，真实短音频仍待人工验收。",
    } as Model;
    const { container } = renderWithRouter(
      <TranscriptionWorkspace model={modelWithContractNote} />,
    );

    expect(
      await screen.findByText(/只接受本次真实资格认证通过的输入格式/),
    ).toBeVisible();
    expect(screen.getByText("契约与费用")).toBeVisible();
    expect(screen.getByText(/目录价为 \$0\.10\/音频小时/)).toBeVisible();
    const input = container.querySelector<HTMLInputElement>(
      'input[type="file"]',
    );
    expect(input).not.toBeNull();
    expect(input).toHaveAttribute(
      "accept",
      ".wav,audio/wav,audio/x-wav",
    );

    fireEvent.change(input!, {
      target: {
        files: [new File(["audio"], "recording.mp3", { type: "audio/mpeg" })],
      },
    });

    expect(
      await screen.findByText(/当前 Managed Provider 仅认证 WAV 音频/),
    ).toBeVisible();
    expect(screen.queryByText("recording.mp3")).not.toBeInTheDocument();
  });

  it("estimates MAI-Transcribe 2 cost from audio duration before submission", async () => {
    stubObjectUrls();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          feature_enabled: true,
          status: "managed_required",
          available: true,
          reason_code: "provider_workload_available",
          certified_input_formats: ["wav"],
        }),
      ),
    );

    expect(
      estimateTranscriptionCostUsd(hourlyTranscriptionModel, 360),
    ).toBeCloseTo(0.01);
    expect(estimateTranscriptionCostUsd(model, 360)).toBeNull();

    const { container } = renderWithRouter(
      <TranscriptionWorkspace model={hourlyTranscriptionModel} />,
    );
    await screen.findByText(/只接受本次真实资格认证通过的输入格式/);
    fireEvent.change(
      container.querySelector<HTMLInputElement>('input[type="file"]')!,
      {
        target: {
          files: [new File(["audio"], "estimate.wav", { type: "audio/wav" })],
        },
      },
    );

    const audio = await screen.findByLabelText("预听 estimate.wav");
    Object.defineProperty(audio, "duration", {
      configurable: true,
      value: 360,
    });
    fireEvent.loadedMetadata(audio);

    expect(
      await screen.findByText(/约 \$0\.010000（360\.0 秒）/),
    ).toBeVisible();
    expect(screen.getByText(/最终以上游回执为准/)).toBeVisible();
  });

  it("uses certified TTS voice and external format without loading the legacy catalog", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/models/provider-workload-control?")) {
        return jsonResponse({
          feature_enabled: true,
          status: "managed_required",
          available: true,
          reason_code: "provider_workload_available",
          certified_voice: "Aoede",
          certified_response_format: "wav",
        });
      }
      throw new Error(`Legacy catalog must not be requested: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithRouter(<SpeechWorkspace model={model} />);

    const voice = await screen.findByLabelText("声线");
    await waitFor(() => expect(voice).toHaveValue("Aoede"));
    expect(screen.getByText("Aoede", { selector: "option" })).toBeInTheDocument();
    expect(
      screen.getByText(/Managed Provider 资格固定使用 Aoede，外部输出为 WAV/),
    ).toBeVisible();
    expect(screen.getByText("WAV")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/multimodal/audio/models",
      expect.anything(),
    );
  });

  it("fails closed for degraded TTS without requesting the legacy catalog", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/models/provider-workload-control?")) {
        return jsonResponse({
          feature_enabled: true,
          status: "degraded_required",
          available: false,
          reason_code: "provider_workload_certification_stale",
          certified_voice: null,
          certified_response_format: null,
        });
      }
      throw new Error(`Legacy catalog must not be requested: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithRouter(<SpeechWorkspace model={model} />);

    expect(
      await screen.findByText(/Provider 控制面处于 degraded_required/),
    ).toHaveTextContent("provider_workload_certification_stale");
    expect(screen.getByLabelText("声线")).toBeDisabled();
    expect(screen.getByLabelText("需要朗读的文字")).toBeDisabled();
    expect(screen.getByRole("button", { name: "生成语音" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("fails closed when the STT public control status cannot be loaded", async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = renderWithRouter(
      <TranscriptionWorkspace model={model} />,
    );

    expect(
      await screen.findByText(/无法读取 Provider 控制面状态/),
    ).toHaveTextContent("已安全阻断本次付费转录");
    expect(
      container.querySelector<HTMLInputElement>('input[type="file"]'),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "选择音频" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "开始转录" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps and renders the sanitized STT success receipt", async () => {
    stubObjectUrls();
    const receipt = providerRouteReceipt("multimodal_transcription");
    stubTranscriptionRequest(200, {
      text: "managed transcript",
      requested_model: model.id,
      actual_model: model.id,
      provider: "openrouter",
      request_id: "request-stt-success",
      usage: {
        audio_seconds: 1,
        input_tokens: null,
        output_tokens: null,
        total_tokens: null,
        cost_usd: null,
        cost_kind: "unavailable",
      },
      provider_route_receipts: [receipt],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          feature_enabled: true,
          status: "managed_required",
          available: true,
          reason_code: "provider_workload_available",
          certified_input_formats: ["wav"],
        }),
      ),
    );

    const { container } = renderWithRouter(
      <TranscriptionWorkspace model={model} />,
    );
    await screen.findByText(/只接受本次真实资格认证通过的输入格式/);
    fireEvent.change(
      container.querySelector<HTMLInputElement>('input[type="file"]')!,
      {
        target: {
          files: [new File(["audio"], "receipt.wav", { type: "audio/wav" })],
        },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "开始转录" }));

    expect(
      await screen.findByText("转录控制面：已纳管 · 1 次 Provider 调用"),
    ).toBeVisible();
    expect(await screen.findByText("managed transcript")).toBeVisible();
    expect(document.body.textContent).not.toContain("must-not-render");
  });

  it("carries an STT failure receipt separately from the error message", async () => {
    stubObjectUrls();
    const receipt = providerRouteReceipt("multimodal_transcription", "failed");
    stubTranscriptionRequest(502, {
      detail: {
        code: "provider_workload_upstream_failed",
        message: "转录服务暂时不可用。",
        route_receipt: receipt,
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          feature_enabled: true,
          status: "managed_required",
          available: true,
          reason_code: "provider_workload_available",
          certified_input_formats: ["wav"],
        }),
      ),
    );

    const { container } = renderWithRouter(
      <TranscriptionWorkspace model={model} />,
    );
    await screen.findByText(/只接受本次真实资格认证通过的输入格式/);
    fireEvent.change(
      container.querySelector<HTMLInputElement>('input[type="file"]')!,
      {
        target: {
          files: [new File(["audio"], "failure.wav", { type: "audio/wav" })],
        },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "开始转录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "转录服务暂时不可用。",
    );
    expect(
      await screen.findByText("转录控制面：已纳管 · 1 次 Provider 调用"),
    ).toBeVisible();
    expect(screen.getByRole("alert")).not.toHaveTextContent("route_receipt");
    expect(screen.getByRole("alert")).not.toHaveTextContent("workrun-");
  });

  it("keeps and renders the sanitized TTS success receipt header", async () => {
    stubObjectUrls();
    const receipt = providerRouteReceipt("multimodal_speech");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/models/provider-workload-control?")) {
        return jsonResponse({
          feature_enabled: true,
          status: "managed_required",
          available: true,
          reason_code: "provider_workload_available",
          certified_voice: "Aoede",
          certified_response_format: "wav",
        });
      }
      if (url === "/api/multimodal/speech") {
        return new Response(new Uint8Array([1, 2, 3]), {
          status: 200,
          headers: {
            "Content-Type": "audio/wav",
            "X-ModelMirror-Actual-Model": model.id,
            "X-ModelMirror-Provider": "openrouter",
            "X-ModelMirror-Provider-Route-Receipt": JSON.stringify(receipt),
          },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithRouter(<SpeechWorkspace model={model} />);
    await waitFor(() => expect(screen.getByLabelText("声线")).toHaveValue("Aoede"));
    fireEvent.change(screen.getByLabelText("需要朗读的文字"), {
      target: { value: "Render the receipt." },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成语音" }));

    expect(
      await screen.findByText("语音生成控制面：已纳管 · 1 次 Provider 调用"),
    ).toBeVisible();
    expect(await screen.findByLabelText("播放生成的语音")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("must-not-render");
  });

  it("carries a TTS failure receipt separately from the error message", async () => {
    const receipt = providerRouteReceipt("multimodal_speech", "failed");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/models/provider-workload-control?")) {
        return jsonResponse({
          feature_enabled: true,
          status: "managed_required",
          available: true,
          reason_code: "provider_workload_available",
          certified_voice: "Aoede",
          certified_response_format: "wav",
        });
      }
      if (url === "/api/multimodal/speech") {
        return new Response(
          JSON.stringify({
            detail: {
              code: "provider_workload_upstream_failed",
              message: "语音服务暂时不可用。",
              route_receipt: receipt,
            },
          }),
          {
            status: 502,
            headers: { "Content-Type": "application/json" },
          },
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithRouter(<SpeechWorkspace model={model} />);
    await waitFor(() => expect(screen.getByLabelText("声线")).toHaveValue("Aoede"));
    fireEvent.change(screen.getByLabelText("需要朗读的文字"), {
      target: { value: "Fail with a receipt." },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成语音" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "语音服务暂时不可用。",
    );
    expect(
      await screen.findByText("语音生成控制面：已纳管 · 1 次 Provider 调用"),
    ).toBeVisible();
    expect(screen.getByRole("alert")).not.toHaveTextContent("route_receipt");
    expect(screen.getByRole("alert")).not.toHaveTextContent("workrun-");
  });
});
