import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { type WorkflowDefinition } from "../../types/workflow";

vi.mock("../workflow/WorkflowEditor", () => ({
  default: ({
    initialDefinition,
    onSave,
    saveLabel,
  }: {
    initialDefinition: WorkflowDefinition;
    onSave: (definition: WorkflowDefinition) => Promise<void>;
    saveLabel: string;
  }) => (
    <button onClick={() => void onSave(initialDefinition)} type="button">
      {saveLabel}
    </button>
  ),
}));

import MetaPlannerV2, { normalizeVisionModelOptions } from "./MetaPlannerV2";
import {
  normalizeGraphPatchPreview,
  normalizeVisionAttachment,
} from "./metaAuthoring";

const CAPABILITY_VERSION = "evoagentx-meta-planner-capabilities-v9";
const VISION_MODEL_ID = "vendor/vision-a";
const VISION_BINDING = {
  entry_id: "xpert_vision",
  model_id: VISION_MODEL_ID,
  routing_mode: "managed_required",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function capabilityPayload(visionModels: unknown[] = []) {
  return {
    version: CAPABILITY_VERSION,
    ir_version: 3,
    supported_ir_versions: [2, 3],
    snapshot_hash: "snapshot-hash",
    generated_at: 1,
    nodes: [
      {
        kind: "workflow_agent",
        title: "工作流智能体",
        planner: { task_binding: "required" },
      },
      {
        kind: "vision_understanding",
        title: "视觉理解",
        planner: { task_binding: "forbidden" },
      },
    ],
    middleware: [],
    external_xperts: [],
    knowledge_bases: [],
    data_tables: [],
    toolsets: [],
    plugins: [],
    prompt_profiles: [],
    vision_models: visionModels,
    default_scope: {
      allowed_node_kinds: ["workflow_agent", "vision_understanding"],
      external_xpert_ids: [],
      knowledge_base_ids: [],
      data_table_ids: [],
      toolset_ids: [],
      plugin_ids: [],
      prompt_profile_ids: [],
      middleware_ids: [],
    },
  };
}

function validVisionModel() {
  return {
    id: VISION_MODEL_ID,
    label: "视觉模型甲",
    safe: true,
    binding: VISION_BINDING,
  };
}

function visionAttachment(maxModelCalls = 20) {
  return {
    port: "selected_file_asset_id",
    source_ref: "input",
    cardinality: "one",
    formats: ["png", "jpeg", "webp", "pdf"],
    max_bytes: 10 * 1024 * 1024,
    max_pages: 20,
    trusted_runtime_input: true,
    model_id: VISION_MODEL_ID,
    managed_required: true,
    max_model_calls: maxModelCalls,
  };
}

describe("Meta Planner V9 视觉授权", () => {
  it("只接受 V9 安全目录中一致的已纳管绑定", () => {
    expect(
      normalizeVisionModelOptions({
        version: CAPABILITY_VERSION,
        vision_models: [
          validVisionModel(),
          {
            id: "vendor/legacy-safe",
            label: "兼容安全模型",
            safe: true,
            binding: {
              entry_id: "xpert_vision",
              model_id: "vendor/legacy-safe",
            },
          },
          {
            id: "vendor/wrong-entry",
            label: "错误入口",
            safe: true,
            binding: {
              entry_id: "meta_agent",
              model_id: "vendor/wrong-entry",
            },
          },
          {
            id: "vendor/mismatch",
            label: "模型不一致",
            safe: true,
            binding: {
              entry_id: "xpert_vision",
              model_id: "vendor/other",
            },
          },
          {
            id: "vendor/missing-safe",
            label: "缺少安全标记",
            binding: {
              entry_id: "xpert_vision",
              model_id: "vendor/missing-safe",
            },
          },
          {
            id: "vendor/guessed-shape",
            label: "错误兼容字段",
            safe: true,
            managed_binding: {
              entry_id: "xpert_vision",
              model_id: "vendor/guessed-shape",
            },
          },
        ],
      }),
    ).toEqual([
      {
        id: "vendor/legacy-safe",
        label: "兼容安全模型",
        safe: true,
        binding: {
          entry_id: "xpert_vision",
          model_id: "vendor/legacy-safe",
        },
      },
      validVisionModel(),
    ]);
    expect(
      normalizeVisionModelOptions({
        version: "evoagentx-meta-planner-capabilities-v8",
        vision_models: [validVisionModel()],
      }),
    ).toEqual([]);
  });

  it("模型 ID 对齐 512 字符契约并拒绝控制字符", () => {
    const acceptedId = `vendor/${"a".repeat(505)}`;
    const rejectedId = `${acceptedId}b`;
    const rows = normalizeVisionModelOptions({
      version: CAPABILITY_VERSION,
      vision_models: [
        {
          id: acceptedId,
          label: "长模型 ID",
          safe: true,
          binding: { entry_id: "xpert_vision", model_id: acceptedId },
        },
        {
          id: rejectedId,
          label: "超长模型 ID",
          safe: true,
          binding: { entry_id: "xpert_vision", model_id: rejectedId },
        },
        {
          id: "vendor/unsafe\u0000id",
          label: "控制字符模型 ID",
          safe: true,
          binding: {
            entry_id: "xpert_vision",
            model_id: "vendor/unsafe\u0000id",
          },
        },
      ],
    });

    expect(rows).toHaveLength(1);
    expect(rows[0]?.id).toBe(acceptedId);
  });

  it("默认不授权视觉，选定独立模型后请求只发送模型 ID", async () => {
    let generationBody: Record<string, unknown> | null = null;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/meta-agent/capabilities") {
          return jsonResponse(capabilityPayload([validVisionModel()]));
        }
        if (url.startsWith("/api/xperts?")) {
          return jsonResponse({ items: [], total: 0 });
        }
        if (url.startsWith("/api/runtime/authoring-proposals?")) {
          return jsonResponse({ items: [] });
        }
        if (
          url === "/api/meta-agent/generate-xpert-candidate" &&
          init?.method === "POST"
        ) {
          generationBody = JSON.parse(String(init.body));
          return jsonResponse({ detail: "测试请求已记录。" }, 422);
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <MetaPlannerV2 />
      </MemoryRouter>,
    );

    const visionCheckbox = await screen.findByRole("checkbox", {
      name: "节点：视觉理解",
    });
    const visionSelect = screen.getByRole("combobox", { name: "视觉模型" });
    expect(visionCheckbox).not.toBeChecked();
    expect(visionSelect).toBeDisabled();

    fireEvent.change(
      screen.getByPlaceholderText(
        "例如：构建一个负责研究、事实核查与审稿协作的智能体",
      ),
      { target: { value: "请生成一个只处理单张视觉附件的事实核查智能体。" } },
    );
    fireEvent.click(visionCheckbox);
    expect(visionSelect).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "生成候选智能体" }),
    ).toBeDisabled();

    fireEvent.change(visionSelect, { target: { value: VISION_MODEL_ID } });
    const generateButton = screen.getByRole("button", {
      name: "生成候选智能体",
    });
    expect(generateButton).toBeEnabled();
    fireEvent.click(generateButton);

    await waitFor(() => expect(generationBody).not.toBeNull());
    expect(generationBody).toMatchObject({
      vision_model_id: VISION_MODEL_ID,
      scope: expect.objectContaining({
        allowed_node_kinds: expect.arrayContaining(["vision_understanding"]),
      }),
    });
    expect(generationBody).not.toHaveProperty("vision_attachment");
    expect(JSON.stringify(generationBody)).not.toContain("asset_id");
    expect(JSON.stringify(generationBody)).not.toContain("selected_file_asset_id");
  });

  it("空目录时安全禁用视觉授权和模型选择", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/meta-agent/capabilities") {
          return jsonResponse(capabilityPayload([]));
        }
        if (url.startsWith("/api/xperts?")) {
          return jsonResponse({ items: [], total: 0 });
        }
        if (url.startsWith("/api/runtime/authoring-proposals?")) {
          return jsonResponse({ items: [] });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <MetaPlannerV2 />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("checkbox", { name: "节点：视觉理解" }),
    ).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "视觉模型" })).toBeDisabled();
    expect(
      screen.getByText("当前没有可用的已纳管视觉模型，视觉理解保持关闭。"),
    ).toBeInTheDocument();
  });

  it("候选与 Headless 预览展示固定附件约束和调用上界", async () => {
    const proposal = {
      proposal_id: "proposal-vision",
      revision: 1,
      apply_key: "apply-key",
      status: "pending",
      kind: "xpert_create",
      title: "视觉候选",
      target_id: null,
      base_revision: null,
      payload: {
        name: "视觉候选",
        description: "固定视觉模型候选",
        tags: [],
        starters: [],
        draft: {
          workflow: {
            id: "workflow-vision",
            title: "视觉候选",
            nodes: [],
            edges: [],
          },
        },
        meta_planner_report: {
          ir_version: 3,
          graph_ir: { version: 3 },
          plan: { summary: "视觉计划", assumptions: [], tasks: [] },
          warnings: [],
        },
      },
      validation: { valid: true, stages: [] },
      applied_resource_id: null,
      updated_at: 1,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/meta-agent/capabilities") {
          return jsonResponse(capabilityPayload([validVisionModel()]));
        }
        if (url.startsWith("/api/xperts?")) {
          return jsonResponse({ items: [], total: 0 });
        }
        if (url.startsWith("/api/runtime/authoring-proposals?")) {
          return jsonResponse({ items: [{ proposal_id: "proposal-vision" }] });
        }
        if (url === "/api/runtime/authoring-proposals/proposal-vision") {
          return jsonResponse(proposal);
        }
        if (url === "/api/meta-agent/authoring/proposals/proposal-vision") {
          return jsonResponse({
            proposal_id: "proposal-vision",
            proposal_revision: 1,
            authoring_protocol_version: "graph-patch-v1",
            ir_version: 3,
            can_author: true,
            graph_checksum: "graph-1",
            candidate_checksum: "candidate-1",
            allowed_node_kinds: ["vision_understanding"],
            compiler_managed_node_kinds: ["input", "output"],
            compatibility: { source_version: 3, lossy: false },
            vision_attachment: visionAttachment(),
          });
        }
        if (url.endsWith("/editor-diff") && init?.method === "POST") {
          return jsonResponse({
            patch: {
              protocol_version: 1,
              proposal_revision: 1,
              expected_graph_checksum: "graph-1",
              expected_candidate_checksum: "candidate-1",
              operations: [{ op: "move_node", ref: "vision", x: 20, y: 30 }],
            },
          });
        }
        if (url.endsWith("/patch/preview") && init?.method === "POST") {
          return jsonResponse({
            preview_checksum: "preview-checksum",
            can_apply: true,
            diagnostics: [],
            warnings: [],
            diff: { layout_changed: true },
            vision_attachment: visionAttachment(40),
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <MetaPlannerV2 />
      </MemoryRouter>,
    );

    expect(await screen.findByText("视觉附件约束")).toBeInTheDocument();
    expect(screen.getByText("单附件，PNG / JPEG / WEBP / PDF")).toBeInTheDocument();
    expect(screen.getByText("最多 10 MiB，PDF 最多 20 页")).toBeInTheDocument();
    expect(screen.getByText("视觉模型甲（vendor/vision-a）")).toBeInTheDocument();
    expect(screen.getByText("最多 20 次视觉模型调用")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "预览候选画布" }));
    const dialog = await screen.findByRole("dialog", { name: "确认类型化变更" });
    expect(within(dialog).getByText("视觉附件约束")).toBeInTheDocument();
    expect(within(dialog).getByText("已纳管")).toBeInTheDocument();
    expect(
      within(dialog).getByText("最多 40 次视觉模型调用"),
    ).toBeInTheDocument();
  });

  it("损坏的 Headless 附件契约不能被归一为可用状态", () => {
    expect(normalizeVisionAttachment(visionAttachment(40))).toEqual(
      visionAttachment(40),
    );
    expect(
      normalizeVisionAttachment({
        ...visionAttachment(),
        source_ref: "untrusted-node",
      }),
    ).toBeNull();
    expect(
      normalizeVisionAttachment({
        ...visionAttachment(),
        trusted_runtime_input: false,
      }),
    ).toBeNull();
  });

  it("损坏的 Preview 附件契约会关闭应用门禁", () => {
    const preview = normalizeGraphPatchPreview({
      preview_checksum: "preview-checksum",
      can_apply: true,
      diagnostics: [],
      warnings: [],
      diff: {},
      vision_attachment: {
        ...visionAttachment(),
        trusted_runtime_input: false,
      },
    });

    expect(preview).not.toBeNull();
    expect(preview?.can_apply).toBe(false);
    expect(preview?.vision_attachment).toBeNull();
  });
});
