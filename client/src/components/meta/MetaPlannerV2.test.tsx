import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

import { plannerModelOptions } from "../../pages/MetaAgentPage";
import MetaPlannerV2, { candidateGenerationOutcome } from "./MetaPlannerV2";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Meta Planner managed compatibility", () => {
  it("does not hide eligible text models behind an arbitrary catalog cap", () => {
    expect(plannerModelOptions.length).toBeGreaterThan(120);
    expect(plannerModelOptions.some((model) => model.id === "openai/gpt-4o-mini")).toBe(
      true,
    );
  });

  it("does not present an unresolved repaired candidate as successful", () => {
    expect(candidateGenerationOutcome({ valid: false }, true)).toEqual({
      error: "候选已保留，但一次定向修复后仍未通过验证，需要人工修复。",
      notice: "",
    });
  });

  it("shows pure Planner nodes as auxiliary capabilities without exposing deferred nodes", async () => {
    const jsonResponse = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/meta-agent/capabilities") {
          return jsonResponse({
            version: "evoagentx-meta-planner-capabilities-v6",
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
                kind: "json_serialize",
                title: "JSON 序列化",
                planner: { task_binding: "forbidden" },
              },
            ],
            middleware: [],
            external_xperts: [],
            knowledge_bases: [],
            toolsets: [],
            plugins: [],
            prompt_profiles: [],
            default_scope: {
              allowed_node_kinds: ["workflow_agent", "json_serialize"],
              external_xpert_ids: [],
              knowledge_base_ids: [],
              toolset_ids: [],
              plugin_ids: [],
              prompt_profile_ids: [],
              middleware_ids: [],
            },
          });
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

    expect(await screen.findByText("JSON 序列化")).toBeInTheDocument();
    expect(screen.getByText("辅助节点")).toBeInTheDocument();
    expect(screen.queryByText("知识检索")).not.toBeInTheDocument();
  });

  it("preserves the paid-call receipt when the follow-up proposal load fails", async () => {
    const receipt = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "meta_agent",
      routing_mode: "managed_required",
      run_reference: "workrun_receipt_preserved",
      status: "passed",
      call_count: 1,
      reason_codes: [],
      calls: [
        {
          call_sequence: 1,
          model_id: "openai/gpt-4o-mini",
          dispatched: true,
          status: "passed",
          total_tokens: 17,
        },
      ],
    };
    const jsonResponse = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/meta-agent/capabilities") {
          return jsonResponse({
            version: "test-snapshot-v1",
            snapshot_hash: "snapshot-hash",
            generated_at: 1,
            nodes: [],
            middleware: [],
            external_xperts: [],
            knowledge_bases: [],
            toolsets: [],
            plugins: [],
            prompt_profiles: [],
            default_scope: {
              allowed_node_kinds: [],
              external_xpert_ids: [],
              knowledge_base_ids: [],
              toolset_ids: [],
              plugin_ids: [],
              prompt_profile_ids: [],
              middleware_ids: [],
            },
          });
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
          return jsonResponse({
            proposal_id: "proposal_receipt_test",
            proposal_revision: 1,
            mode: "create",
            target_xpert_id: null,
            base_revision: null,
            plan: { summary: "Test plan", assumptions: [], tasks: [] },
            candidate: {
              name: "Receipt candidate",
              description: "Test candidate",
              tags: [],
              starters: [],
              draft: {
                workflow: {
                  id: "workflow_receipt_test",
                  title: "Receipt test",
                  nodes: [],
                  edges: [],
                },
              },
            },
            validation: { valid: true, stages: [] },
            warnings: [],
            repair_used: false,
            capability_snapshot_version: "test-snapshot-v1",
            capability_snapshot_hash: "snapshot-hash",
            provider_route_receipts: receipt,
          });
        }
        if (url === "/api/runtime/authoring-proposals/proposal_receipt_test") {
          return jsonResponse({ detail: "proposal reload failed" }, 500);
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

    await screen.findByText("test-snapshot-v1");
    fireEvent.change(
      screen.getByPlaceholderText(
        "例如：构建一个负责研究、事实核查与审稿协作的智能体",
      ),
      { target: { value: "Generate a bounded candidate for receipt testing." } },
    );
    fireEvent.click(screen.getByRole("button", { name: "生成候选智能体" }));

    await screen.findByText("proposal reload failed");
    await waitFor(() => {
      expect(screen.getByText("已纳管")).toBeInTheDocument();
      expect(screen.getByText("1 次模型调用")).toBeInTheDocument();
    });
  });

  it("renders server-authored control-flow evidence without inferring native handles", async () => {
    const jsonResponse = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/meta-agent/capabilities") {
          return jsonResponse({
            version: "test-control-flow-v1",
            snapshot_hash: "snapshot-hash",
            generated_at: 1,
            nodes: [],
            middleware: [],
            external_xperts: [],
            knowledge_bases: [],
            toolsets: [],
            plugins: [],
            prompt_profiles: [],
            default_scope: {
              allowed_node_kinds: [],
              external_xpert_ids: [],
              knowledge_base_ids: [],
              toolset_ids: [],
              plugin_ids: [],
              prompt_profile_ids: [],
              middleware_ids: [],
            },
          });
        }
        if (url.startsWith("/api/xperts?")) {
          return jsonResponse({ items: [], total: 0 });
        }
        if (url.startsWith("/api/runtime/authoring-proposals?")) {
          return jsonResponse({ items: [{ proposal_id: "proposal-control-flow" }] });
        }
        if (url === "/api/runtime/authoring-proposals/proposal-control-flow") {
          return jsonResponse({
            proposal_id: "proposal-control-flow",
            revision: 1,
            status: "pending",
            kind: "xpert_create",
            title: "Control flow candidate",
            validation: { valid: true, stages: [] },
            payload: {
              name: "Control flow candidate",
              description: "Static path evidence",
              tags: [],
              starters: [],
              draft: { workflow: { id: "wf", title: "wf", nodes: [], edges: [] } },
              meta_planner_report: {
                validation: { valid: true, stages: [] },
                graph_ir: {
                  control_flow_report: {
                    version: 1,
                    router_count: 1,
                    scenario_count: 2,
                    final_source_count: 2,
                    scenarios: [
                      {
                        id: "scenario-1",
                        outcomes: ["router:matched"],
                        success_sources: ["approved"],
                        error_sources: [],
                      },
                    ],
                  },
                },
                warnings: [],
              },
            },
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

    expect(await screen.findByText("控制流静态证据")).toBeInTheDocument();
    expect(screen.getByText("1 路由 · 2 场景 · 2 成功来源")).toBeInTheDocument();
    expect(screen.getByText(/router:matched/)).toBeInTheDocument();
    expect(screen.queryByText(/sourceHandle/)).not.toBeInTheDocument();
  });

  it("previews a V3 editor diff before applying it and never uses whole-payload PATCH", async () => {
    let revision = 1;
    let applyCalls = 0;
    const proposal = () => ({
      proposal_id: "proposal_headless",
      revision,
      apply_key: "apply-key",
      status: "pending",
      kind: "xpert_create",
      title: "Meta Planner: Headless",
      target_id: null,
      base_revision: null,
      payload: {
        name: "Headless",
        description: "Typed authoring",
        tags: [],
        starters: [],
        draft: {
          workflow: {
            id: "workflow_headless",
            title: "Headless",
            nodes: [],
            edges: [],
          },
        },
        meta_planner_report: {
          ir_version: 3,
          graph_ir: { version: 3 },
          plan: { summary: "Headless plan", assumptions: [], tasks: [] },
          warnings: [],
        },
      },
      validation: { valid: true, stages: [] },
      applied_resource_id: null,
      updated_at: 1,
    });
    const jsonResponse = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/meta-agent/capabilities") {
        return jsonResponse({
          version: "snapshot-v3",
          ir_version: 3,
          supported_ir_versions: [2, 3],
          snapshot_hash: "snapshot-hash",
          generated_at: 1,
          nodes: [],
          middleware: [],
          external_xperts: [],
          knowledge_bases: [],
          toolsets: [],
          plugins: [],
          prompt_profiles: [],
          default_scope: {
            allowed_node_kinds: [],
            external_xpert_ids: [],
            knowledge_base_ids: [],
            toolset_ids: [],
            plugin_ids: [],
            prompt_profile_ids: [],
            middleware_ids: [],
          },
        });
      }
      if (url.startsWith("/api/xperts?")) return jsonResponse({ items: [], total: 0 });
      if (url.startsWith("/api/runtime/authoring-proposals?")) {
        return jsonResponse({ items: [{ proposal_id: "proposal_headless" }] });
      }
      if (url === "/api/runtime/authoring-proposals/proposal_headless") {
        return jsonResponse(proposal());
      }
      if (url === "/api/meta-agent/authoring/proposals/proposal_headless") {
        return jsonResponse({
          proposal_id: "proposal_headless",
          proposal_revision: revision,
          authoring_protocol_version: "graph-patch-v1",
          can_author: true,
          graph_checksum: `graph-${revision}`,
          candidate_checksum: `candidate-${revision}`,
          graph_ir: { version: 3 },
          allowed_node_kinds: ["input", "output", "workflow_agent"],
          compiler_managed_node_kinds: ["input", "output"],
          compatibility: { source_version: 3, lossy: false },
        });
      }
      if (url.endsWith("/editor-diff") && init?.method === "POST") {
        return jsonResponse({
          patch: {
            protocol_version: 1,
            proposal_revision: revision,
            expected_graph_checksum: `graph-${revision}`,
            expected_candidate_checksum: `candidate-${revision}`,
            operations: [{ op: "move_node", ref: "agent-main", x: 20, y: 30 }],
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
        });
      }
      if (url.endsWith("/patch/apply") && init?.method === "POST") {
        applyCalls += 1;
        revision = 2;
        return jsonResponse({
          version: "meta-planner-headless-authoring-v1",
          proposal_id: "proposal_headless",
          proposal_revision: revision,
          status: "pending",
          validation: { valid: true, stages: [] },
          graph_checksum: "graph-2",
          candidate_checksum: "candidate-2",
          receipt_count: 1,
        });
      }
      if (url === "/api/runtime/authoring-proposals/proposal_headless" && init?.method === "PATCH") {
        throw new Error("V3 authoring must not use whole-payload PATCH");
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <MetaPlannerV2 />
      </MemoryRouter>,
    );

    await screen.findByText("无头编排已启用。", { exact: false });
    fireEvent.click(screen.getByRole("button", { name: "预览候选画布" }));

    await screen.findByRole("dialog", { name: "确认类型化变更" });
    expect(applyCalls).toBe(0);
    expect(screen.getByText("move_node")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认应用" }));

    await screen.findByText("Proposal r2");
    expect(applyCalls).toBe(1);
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          String(url) === "/api/runtime/authoring-proposals/proposal_headless" &&
          (init as RequestInit | undefined)?.method === "PATCH",
      ),
    ).toBe(false);
  });
});
