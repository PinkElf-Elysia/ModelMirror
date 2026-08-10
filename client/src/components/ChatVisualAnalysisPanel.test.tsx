import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatVisualAnalysisPanel, {
  parseVisualAnalysisPages,
  type ChatVisualAnalysisState,
} from "./ChatVisualAnalysisPanel";

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const capability = {
  version: "modelmirror-file-capabilities-v2",
  registry_version: "modelmirror-file-formats-v5",
  requested_purpose: "chat",
  requested_model_id: "vendor/vision",
  model_specific: false,
  capabilities: [
    {
      purpose: "chat",
      input_kind: "visual_analysis",
      families: ["document", "image"],
      max_bytes_per_file: 10 * 1024 * 1024,
      max_files_per_request: 1,
      max_total_bytes_per_request: 10 * 1024 * 1024,
      size_measure: "binary",
      transport: "multipart",
      retention: "temporary",
      support_level: "specialized",
      interaction_status: "ready",
      parser_id: "chat.one_shot_visual_analysis",
      ui_entrypoint: "/chat/:modelId",
      status_reason: null,
      handling_options: [],
      analysis_options: [
        {
          mode: "vision",
          format_ids: ["jpeg", "pdf", "png", "webp"],
          provider: "explicit_connection",
          paid: false,
          max_pages: 20,
          max_prompt_chars: 2000,
          requires_explicit_target: true,
          interaction_status: "ready",
          status_reason: null,
        },
      ],
      formats: [
        {
          format_id: "png",
          family: "image",
          extensions: [".png"],
          media_types: ["image/png"],
          interaction_status: "ready",
          status_reason: null,
        },
      ],
    },
  ],
};

const target = {
  target_id: "target_exact",
  mode: "vision",
  connection_id: "connection_exact",
  connection_name: "Explicit connection",
  model_id: "vendor/vision",
  model_name: "Exact vision model",
  provider: "newapi",
  paid: false,
  cost_disclosure: "The selected connection may charge for token use.",
};

const artifact = {
  version: "modelmirror-file-analysis-artifact-v1",
  asset_id: "asset_1",
  source_filename: "synthetic.png",
  source_sha256: "a".repeat(64),
  format: "png",
  mode: "vision",
  target_id: "target_exact",
  connection_name: "Explicit connection",
  model_id: "vendor/vision",
  selected_pages: [1],
  sections: [{ kind: "visual_summary", text: "公开合成图像摘要", page: 1 }],
  warnings: [],
  processed_pages: 1,
  failed_pages: [],
  extracted_chars: 9,
  truncated: false,
};

afterEach(() => vi.restoreAllMocks());

describe("ChatVisualAnalysisPanel", () => {
  it("parses bounded explicit PDF page selections", () => {
    expect(parseVisualAnalysisPages("1-3,5,3")).toEqual([1, 2, 3, 5]);
    expect(() => parseVisualAnalysisPages("1-21")).toThrow("最多选择 20 页");
    expect(() => parseVisualAnalysisPages("0")).toThrow("从 1 开始");
  });

  it("keeps upload local until confirmation, then previews and chooses each destination", async () => {
    const requests: Array<{ url: string; method: string; body?: unknown }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      let body: unknown;
      if (typeof init?.body === "string") body = JSON.parse(init.body);
      requests.push({ url, method, body });
      if (url.startsWith("/api/files/capabilities")) return json(capability);
      if (url === "/api/files/analysis-targets") {
        return json({ version: "modelmirror-file-analysis-targets-v1", items: [target] });
      }
      if (url.startsWith("/api/files/analyses?")) return json({ items: [], total: 0 });
      if (url === "/api/files" && method === "POST") {
        return json({
          asset_id: "asset_1",
          purpose: "chat",
          scope_id: "chat-scope",
          display_name: "synthetic.png",
          format: "png",
          media_type: "image/png",
          byte_size: 10,
          status: "ready",
          expires_at: "2026-08-10T00:00:00Z",
          created_at: "2026-08-09T00:00:00Z",
          updated_at: "2026-08-09T00:00:00Z",
        }, 201);
      }
      if (url.endsWith("/analysis-preflight")) {
        return json({
          asset_id: "asset_1",
          mode: "vision",
          target,
          format: "png",
          page_count: 1,
          selected_pages: [1],
          prompt_sha256: "b".repeat(64),
          config_digest: "c".repeat(64),
          paid_confirmation_required: false,
          cost_disclosure: target.cost_disclosure,
          privacy_disclosure: "Only selected rendered pages are sent.",
        });
      }
      if (url.endsWith("/analysis-confirm")) {
        return json({
          asset_id: "asset_1",
          confirmation_revision: 1,
          config_digest: "c".repeat(64),
          prompt_sha256: "b".repeat(64),
        });
      }
      if (url.endsWith("/analyses") && method === "POST") {
        return json({
          analysis_id: "analysis_1",
          asset_id: "asset_1",
          scope_id: "chat-scope",
          mode: "vision",
          target_id: "target_exact",
          selected_pages: [1],
          page_count: 1,
          processed_pages: 1,
          status: "completed",
          result_artifact_id: "artifact_1",
          result: artifact,
          actual_cost_usd: null,
          error_code: null,
          created_at: "2026-08-09T00:00:00Z",
          updated_at: "2026-08-09T00:00:01Z",
          completed_at: "2026-08-09T00:00:01Z",
        }, 202);
      }
      if (url.includes("/confirm?") && method === "POST") {
        return json({
          asset_id: "asset_1",
          handling: "extract",
          confirmation_revision: 2,
          confirmed_at: "2026-08-09T00:00:02Z",
          analysis_artifact_id: "artifact_1",
        });
      }
      if (url.includes("/documents/from-file-analysis")) {
        return json({ id: "doc_1" });
      }
      throw new Error(`Unexpected request: ${method} ${url}`);
    });

    const states: ChatVisualAnalysisState[] = [];
    const host = document.createElement("div");
    document.body.appendChild(host);
    Object.defineProperty(host, "getBoundingClientRect", {
      value: () => ({ left: 0, top: -216, width: 390, height: 800 }),
    });
    const inputBoundary = document.createElement("textarea");
    Object.defineProperty(inputBoundary, "getBoundingClientRect", {
      value: () => ({ top: 460 }),
    });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });

    render(
      <ChatVisualAnalysisPanel
        disabled={false}
        discardVersion={0}
        drawerHost={host}
        inputBoundary={inputBoundary}
        knowledgeBases={[{ id: "kb_1", name: "验收资料库" }]}
        modelId="vendor/vision"
        onError={(message) => { throw new Error(message); }}
        onStateChange={(state) => states.push(state)}
        resetVersion={0}
        scopeId="chat-scope"
      />,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "视觉/OCR" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "视觉/OCR" }));
    const region = await screen.findByRole("region", { name: "一次性视觉 / OCR" });
    await waitFor(() => {
      expect(region).toHaveStyle({ top: "0px", height: "460px" });
    });
    const fileInput = document.querySelector('input[type="file"][accept=".pdf,.png,.jpg,.jpeg,.webp"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["synthetic"], "synthetic.png", { type: "image/png" })] },
    });
    await screen.findByText("synthetic.png · PNG");
    expect(requests.some((item) => item.url.includes("analysis-preflight"))).toBe(false);

    fireEvent.change(screen.getByPlaceholderText("例如：读取表格并概括异常趋势"), {
      target: { value: "只识别公开标签" },
    });
    fireEvent.click(screen.getByRole("button", { name: "本地预检、确认并开始" }));
    await screen.findByText("公开合成图像摘要");
    const preflight = requests.find((item) => item.url.endsWith("analysis-preflight"));
    expect(preflight?.body).toMatchObject({
      target_id: "target_exact",
      prompt: "只识别公开标签",
      selected_pages: [],
    });
    expect(preflight?.body).not.toHaveProperty("paid_acknowledged");

    fireEvent.click(screen.getByRole("button", { name: "用于本轮发送" }));
    await waitFor(() => expect(states.at(-1)?.allConfirmed).toBe(true));
    const sendConfirmation = requests.find((item) => item.url.includes("/confirm?"));
    expect(sendConfirmation?.body).toMatchObject({
      analysis_artifact_id: "artifact_1",
      analysis_prompt: "只识别公开标签",
    });

    fireEvent.change(screen.getByLabelText("选择保存到的资料库"), {
      target: { value: "kb_1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存识别结果到资料库" }));
    await screen.findByText("识别结果已保存为资料库派生文档；Chat 原件生命周期未延长。");
    expect(requests.find((item) => item.url.includes("from-file-analysis"))?.body).toEqual({
      asset_id: "asset_1",
      analysis_artifact_id: "artifact_1",
      chat_scope_id: "chat-scope",
    });
    host.remove();
  });

  it("fails closed when capability or live targets are unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).startsWith("/api/files/capabilities")) return json(capability);
      if (String(input) === "/api/files/analysis-targets") {
        return json({ version: "modelmirror-file-analysis-targets-v1", items: [] });
      }
      return json({ items: [], total: 0 });
    });
    render(
      <ChatVisualAnalysisPanel
        disabled={false}
        discardVersion={0}
        drawerHost={document.body}
        knowledgeBases={[]}
        modelId="vendor/vision"
        onError={() => undefined}
        onStateChange={() => undefined}
        resetVersion={0}
        scopeId="chat-scope"
      />,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "视觉/OCR" })).toBeDisabled());
  });

  it("accepts only PDF when the sole live mode is provider OCR", async () => {
    const ocrCapability = structuredClone(capability);
    ocrCapability.capabilities[0].analysis_options = [
      {
        mode: "provider_ocr",
        format_ids: ["pdf"],
        provider: "openrouter_mistral_ocr",
        paid: true,
        max_pages: 20,
        max_prompt_chars: 2000,
        requires_explicit_target: true,
        interaction_status: "ready",
        status_reason: null,
      },
    ];
    const ocrTarget = {
      ...target,
      target_id: "target_ocr",
      mode: "provider_ocr",
      provider: "openrouter",
      paid: true,
    };
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      requests.push(url);
      if (url.startsWith("/api/files/capabilities")) return json(ocrCapability);
      if (url === "/api/files/analysis-targets") {
        return json({
          version: "modelmirror-file-analysis-targets-v1",
          items: [ocrTarget],
        });
      }
      if (url.startsWith("/api/files/analyses?")) {
        return json({ items: [], total: 0 });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    const onError = vi.fn();
    render(
      <ChatVisualAnalysisPanel
        disabled={false}
        discardVersion={0}
        drawerHost={document.body}
        knowledgeBases={[]}
        modelId="text/model"
        onError={onError}
        onStateChange={() => undefined}
        resetVersion={0}
        scopeId="chat-scope"
      />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "视觉/OCR" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "视觉/OCR" }));
    const fileInput = document.querySelector(
      'input[type="file"][accept=".pdf"]',
    ) as HTMLInputElement;
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput, {
      target: {
        files: [new File(["synthetic"], "synthetic.png", { type: "image/png" })],
      },
    });
    await waitFor(() =>
      expect(onError).toHaveBeenCalledWith(
        "当前没有实时可调用的视觉模型；供应商 OCR 只接受 PDF。",
      ),
    );
    expect(requests).not.toContain("/api/files");
  });

  it("adopts an existing scanned PDF asset without uploading it again", async () => {
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      requests.push(url);
      if (url.startsWith("/api/files/capabilities")) return json(capability);
      if (url === "/api/files/analysis-targets") {
        return json({
          version: "modelmirror-file-analysis-targets-v1",
          items: [target],
        });
      }
      if (url.startsWith("/api/files/analyses?")) {
        return json({ items: [], total: 0 });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    render(
      <ChatVisualAnalysisPanel
        disabled={false}
        discardVersion={0}
        drawerHost={document.body}
        knowledgeBases={[]}
        modelId="vendor/vision"
        onError={() => undefined}
        onStateChange={() => undefined}
        resetVersion={0}
        scopeId="chat-scope"
      />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "视觉/OCR" })).toBeEnabled(),
    );
    act(() => {
      window.dispatchEvent(
        new CustomEvent("modelmirror:open-chat-visual-analysis", {
          detail: {
            assetId: "asset_scan",
            displayName: "scan.pdf",
            format: "pdf",
            byteSize: 2048,
          },
        }),
      );
    });
    await screen.findByRole("region", { name: "一次性视觉 / OCR" });
    expect(screen.getByText("scan.pdf · PDF")).toBeVisible();
    expect(
      screen.getByText(
        "已接管扫描 PDF 原件，未重新上传且尚未外发。请选择页码、明确目标并确认。",
      ),
    ).toBeVisible();
    expect(requests).not.toContain("/api/files");
  });
});
