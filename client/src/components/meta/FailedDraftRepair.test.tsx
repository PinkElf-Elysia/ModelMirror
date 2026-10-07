import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import FailedDraftRepair, { normalizeRecoveryState } from "./FailedDraftRepair";

vi.mock("../../data/models", () => ({ models: [{ id: "test/repair", name: "测试模型" }] }));

export function recoveryFixture() {
  return {
    mode: "recovery", proposal_id: "proposal-repair", proposal_revision: 1,
    can_author: true, can_approve: false, graph_checksum: "a".repeat(64), candidate_checksum: "b".repeat(64),
    diagnostics: [{ code: "missing_records", message: "写入节点缺少可信记录输入", severity: "error" }],
    recovery: {
      status: "editable", executable: false, artifact_checksum: "c".repeat(64), selected_attempt_id: 2,
      intent: { name: "合成修复样例", nodes: [
        { ref: "lookup", kind: "data_table_query", title: "查询记录", config: {}, inputs: [], outputs: [{ port: "result", variable: "rows" }] },
        { ref: "write", kind: "data_table_update", title: "更新记录", config: { values: { status: "需复核" } }, inputs: [], outputs: [{ port: "result" }] },
      ], control_edges: [{ source_ref: "lookup", outcome_ref: "success", target_ref: "write" }] },
      node_contracts: { data_table_query: { output_ports: ["result"] }, data_table_update: { input_ports: ["records"] } },
      current_diagnostics: { phase_results: [{ id: "authorization", status: "failed" }, { id: "compile", status: "blocked" }], issues: [{ code: "SCHEMA_VALIDATION_FAILED", category: "node_config", node_ref: "write", location: ["nodes", 1, "inputs"] }] },
      attempts: [{ attempt_id: 2, diagnostic_subject: "repair_input", diagnostics: { issues: [{ code: "PATCH_CONTROL_EDGE_EXISTS", category: "patch_apply" }] } }],
    },
  };
}

afterEach(() => vi.unstubAllGlobals());

it("shows blocked path proofs separately from an empty downstream issue list", () => {
  const raw = recoveryFixture();
  const state = normalizeRecoveryState({ ...raw, recovery: { ...raw.recovery, current_diagnostics: {
    ...raw.recovery.current_diagnostics,
    control_proof_checks: [
      { id: "structure", status: "passed" },
      { id: "predicate_domains", status: "failed" },
      { id: "data_availability", status: "blocked", blocked_by: "predicate_domains" },
    ],
  } } })!;
  render(<FailedDraftRepair state={state} onApplied={vi.fn()} />);
  expect(screen.getByLabelText("控制流证明状态")).toHaveTextContent("条件输入与真值：失败");
  expect(screen.getByText("路径数据可用性：被阻断（条件输入与真值尚未通过）")).toBeInTheDocument();
  expect(screen.queryByText("路径数据可用性：通过")).not.toBeInTheDocument();
});

function recipeFixture() {
  const raw = recoveryFixture();
  return { ...raw, diagnostics: [{ code: "RECIPE_REPEATED_NODE", message: "控制流重复引用同一节点", severity: "error" }], recovery: {
    ...raw.recovery, source_format: "recipe_v1", intent: null,
    recipe: { generation_protocol_version: 1, nodes: raw.recovery.intent.nodes,
      control_flow: [{ type: "node", node_ref: "lookup" }, { type: "node", node_ref: "write" }, { type: "node", node_ref: "write" }] },
    current_diagnostics: { phase_results: [{ id: "recipe_lowering", status: "failed" }, { id: "authorization", status: "blocked" }],
      issues: [{ code: "RECIPE_REPEATED_NODE", category: "recipe_lowering", node_ref: "write", location: ["control_flow", 2, "node_ref"], recipe_detail: { first_location: ["control_flow", 1, "node_ref"] } }] },
  } };
}

function semanticRecipeFixture() {
  const raw = recipeFixture();
  return { ...raw, recovery: { ...raw.recovery, semantic_repair: {
    protocol_version: "recipe_edits_v1", operations_schema: { type: "object" }, edit_contract: {
      max_operations: 16, max_bytes: 65536, update_node_refs: ["lookup", "write"], cloneable_agent_refs: [],
      operations: {
        update_node: { required_fields: ["op", "node_ref"], optional_fields: ["title", "description", "config", "inputs"] },
        clone_agent: { required_fields: ["op", "source_ref", "ref", "title", "task_input"], optional_fields: ["description", "role_prompt"] },
        replace_control_flow: { required_fields: ["op", "control_flow"], optional_fields: [] },
        set_final_output: { required_fields: ["op", "final_output"], optional_fields: [] },
      },
    },
  } } };
}

describe("未展开 Recipe 的语义修复", () => {
  it("does not present an unexecuted comparison as resolved", () => {
    const raw = semanticRecipeFixture();
    const state = normalizeRecoveryState({ ...raw, recovery: { ...raw.recovery, attempts: [{ attempt_id: 2, diagnostic_subject: "repair_input",
      diagnostics: { recipe_repair_progress: { assessment: "not_rechecked", persisting_issue_count: null, no_longer_observed_issue_count: null } } }] } })!;
    render(<FailedDraftRepair state={state} onApplied={vi.fn()} />);
    expect(screen.getByText("修复未到达原失败检查，旧问题尚未复核，不能判定已消除。")).toBeInTheDocument();
  });
  it("previews semantic edits without mutation and applies only that preview", async () => {
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(
      JSON.parse(String(init?.body)).preview_checksum ? { proposal_revision: 2 } : { preview_checksum: "d".repeat(64), can_apply: true, diagnostics: [] })));
    vi.stubGlobal("fetch", fetcher);
    const applied = vi.fn(async () => undefined);
    render(<FailedDraftRepair state={normalizeRecoveryState(semanticRecipeFixture())!} onApplied={applied} />);
    expect(screen.getByRole("button", { name: "定向模型修复" })).toBeInTheDocument();
    expect(screen.getByText("操作字段契约")).toBeInTheDocument();
    const operations = [{ op: "update_node", node_ref: "write", title: "保留原业务更新" },
      { op: "replace_control_flow", control_flow: recipeFixture().recovery.recipe.control_flow.slice(0, 2) }];
    fireEvent.change(screen.getByLabelText("语义修复操作 JSON"), { target: { value: JSON.stringify(operations) } });
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    await screen.findByText("修复预览通过，尚未写回");
    const body = JSON.parse(String(fetcher.mock.calls[0][1]?.body));
    expect(body.patch).toMatchObject({ protocol_version: "recipe_edits_v1", operations });
    expect(applied).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认应用修复" }));
    await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ ...body, preview_checksum: "d".repeat(64) });
  });

  it.each([
    [{ op: "replace_control_flow", config: {} }], [{ op: "update_node", config: {} }],
    [{ op: "update_node", node_ref: "write", sourceHandle: "out" }],
  ])("rejects malformed operation fields before preview: %j", async operation => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    render(<FailedDraftRepair state={normalizeRecoveryState(semanticRecipeFixture())!} onApplied={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("语义修复操作 JSON"), { target: { value: JSON.stringify([operation]) } });
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("服务端字段契约");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each(["recipe_input_draft_v1", "recipe_resource_draft_v1", "unknown"])("cannot enable semantic operations for %s", format => {
    const raw = semanticRecipeFixture();
    const state = normalizeRecoveryState({ ...raw, recovery: { ...raw.recovery, source_format: format } })!;
    expect(state.recovery.semantic_repair).toBeUndefined();
  });

  it("loads an explicitly requested semantic suggestion only into local edits", async () => {
    const operations = [{ op: "update_node", node_ref: "write", title: "核对后更新" }];
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify(url.endsWith("/preflight") ? { ...body, can_dispatch: true, max_calls: 1,
        repair_protocol: "recipe_edits_v1", authorization_checksum: "d".repeat(64), authorization_token: "e".repeat(32) + ".9999999999." + "f".repeat(64),
        expires_at: 9999999999, route: { label: "离线模拟" }, outbound: { messages: [] } } : { status: "suggested", request_id: body.request_id,
        patch: { protocol_version: "recipe_edits_v1", proposal_revision: 1, expected_graph_checksum: "a".repeat(64), expected_candidate_checksum: "b".repeat(64), operations } }));
    });
    vi.stubGlobal("fetch", fetcher);
    const applied = vi.fn();
    render(<FailedDraftRepair state={normalizeRecoveryState(semanticRecipeFixture())!} onApplied={applied} />);
    fireEvent.click(screen.getByRole("button", { name: "定向模型修复" }));
    fireEvent.change(screen.getByLabelText("修复模型"), { target: { value: "test/repair" } });
    fireEvent.click(screen.getByRole("button", { name: "查看本次外发内容" }));
    fireEvent.click(await screen.findByLabelText(/我确认以上内容/));
    fireEvent.click(screen.getByRole("button", { name: "授权并执行一次修复" }));
    await screen.findByRole("button", { name: "载入人工编辑区" });
    expect(screen.getByLabelText("语义修复操作 JSON")).toHaveValue("[]");
    fireEvent.click(screen.getByRole("button", { name: "载入人工编辑区" }));
    expect(screen.getByLabelText("语义修复操作 JSON")).toHaveValue(JSON.stringify(operations, null, 2));
    expect(screen.getByRole("button", { name: "确认应用修复" })).toBeDisabled();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(applied).not.toHaveBeenCalled();
  });
});

function resourceDraftFixture() {
  const raw = recipeFixture();
  return { ...raw, diagnostics: [], recovery: { ...raw.recovery,
    source_format: "recipe_resource_draft_v1",
    recipe: { ...raw.recovery.recipe, resources: [{ kind: "data_table", resource_id: "table-synthetic", target_ref: "write" }] },
    current_diagnostics: { phase_results: [{ id: "intent_parse", status: "failed" }],
      issues: [{ code: "SCHEMA_VALIDATION_FAILED", category: "node_config", location: ["resources", 0, "kind"] }] },
  } };
}

function inputDraftFixture(protocol: number | null = null) {
  const raw = recipeFixture();
  return { ...raw, diagnostics: [], recovery: { ...raw.recovery,
    source_format: "recipe_input_draft_v1",
    recipe: { ...raw.recovery.recipe, generation_protocol_version: protocol,
      nodes: raw.recovery.recipe.nodes.map(node => ({ ...node, inputs: null })) },
    current_diagnostics: { phase_results: [{ id: "intent_parse", status: "failed" }, { id: "compile", status: "blocked" }],
      issues: [{ code: "RECIPE_INPUT_MODE_INVALID", category: "intent_parse", location: ["nodes", 0, "inputs"], schema_detail: { expected: "array", actual: "null", input_mode: "explicit" } }] },
  } };
}

describe("输入与协议失败恢复", () => {
  it("retains null without silently fixing inputs or confirming the protocol", () => {
    const state = normalizeRecoveryState(inputDraftFixture())!;
    expect(state.can_author).toBe(true);
    expect(state.recovery.recipe?.nodes[0].inputs).toBeNull();
    render(<FailedDraftRepair state={state} onApplied={vi.fn()} />);
    expect(screen.getByLabelText("节点输入引用 JSON")).toHaveValue(JSON.stringify([
      { node_ref: "lookup", inputs: null }, { node_ref: "write", inputs: null },
    ], null, 2));
    expect(screen.getByLabelText("确认采用生成协议 V1")).not.toBeChecked();
    expect(screen.getByText("要求：输入引用数组；实际：null")).toBeInTheDocument();
    expect(screen.getByText("编译：未执行")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "校验并预览修复" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "定向模型修复" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("控制结构 JSON")).not.toBeInTheDocument();
  });

  it("sends only explicit changed inputs and binds apply to that preview", async () => {
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify(body.preview_checksum ? { proposal_revision: 2 } : { preview_checksum: "d".repeat(64), can_apply: true, diagnostics: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const applied = vi.fn(async () => undefined);
    render(<FailedDraftRepair state={normalizeRecoveryState(inputDraftFixture())!} onApplied={applied} />);
    fireEvent.change(screen.getByLabelText("节点输入引用 JSON"), { target: { value: JSON.stringify([{ node_ref: "lookup", inputs: [] }]) } });
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("确认采用生成协议 V1"));
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    await screen.findByText("修复预览通过，尚未写回");
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.patch.protocol_version).toBe("recipe_inputs_v1");
    expect(body.patch.operations).toEqual([{ op: "confirm_recipe_protocol" }, { op: "replace_recipe_inputs", node_ref: "lookup", inputs: [] }]);
    expect(applied).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认应用修复" }));
    await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ ...body, preview_checksum: "d".repeat(64) });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("invalidates preview on protocol changes and locks after a conflict", async () => {
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return body.preview_checksum ? new Response(JSON.stringify({ detail: { message: "版本冲突" } }), { status: 409 })
        : new Response(JSON.stringify({ preview_checksum: "d".repeat(64), can_apply: true, diagnostics: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<FailedDraftRepair state={normalizeRecoveryState(inputDraftFixture())!} onApplied={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("确认采用生成协议 V1"));
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    await screen.findByText("修复预览通过，尚未写回");
    fireEvent.click(screen.getByLabelText("确认采用生成协议 V1"));
    expect(screen.getByRole("button", { name: "确认应用修复" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText("确认采用生成协议 V1"));
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    await screen.findByText("修复预览通过，尚未写回");
    fireEvent.click(screen.getByRole("button", { name: "确认应用修复" }));
    await screen.findByText("版本冲突");
    expect(screen.getByLabelText("节点输入引用 JSON")).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each([
    [{ node_ref: "lookup", inputs: [], config: {} }],
    [{ node_ref: "lookup", inputs: [] }, { node_ref: "lookup", inputs: [] }],
    [{ node_ref: "unknown", inputs: [] }],
  ])("rejects invalid local input edits without network requests: %j", async (...rows) => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    render(<FailedDraftRepair state={normalizeRecoveryState(inputDraftFixture(1))!} onApplied={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("节点输入引用 JSON"), { target: { value: JSON.stringify(rows) } });
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects unsupported protocol versions rather than offering confirmation", () => {
    expect(normalizeRecoveryState(inputDraftFixture(2))?.can_author).toBe(false);
  });
});

describe("资源描述 Schema 失败恢复", () => {
  it("shows the retained invalid binding without exposing node or paid repair controls", () => {
    const state = normalizeRecoveryState(resourceDraftFixture())!;
    expect(state.can_author).toBe(true);
    render(<FailedDraftRepair state={state} onApplied={vi.fn()} />);
    expect(screen.getByText("resources.0.kind")).toBeInTheDocument();
    expect(screen.getByLabelText("Agent 资源绑定 JSON")).toHaveValue(JSON.stringify(resourceDraftFixture().recovery.recipe.resources, null, 2));
    expect(screen.queryByLabelText("控制结构 JSON")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定向模型修复" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认应用修复" })).toBeDisabled();
  });

  it("permits an empty binding set only through separate preview and checksum-bound apply", async () => {
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify(body.preview_checksum ? { proposal_revision: 2 } : { preview_checksum: "d".repeat(64), can_apply: true, diagnostics: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const applied = vi.fn(async () => undefined);
    render(<FailedDraftRepair state={normalizeRecoveryState(resourceDraftFixture())!} onApplied={applied} />);
    fireEvent.change(screen.getByLabelText("Agent 资源绑定 JSON"), { target: { value: "[]" } });
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    await screen.findByText("修复预览通过，尚未写回");
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.patch.protocol_version).toBe("recipe_resource_bindings_v1");
    expect(body.patch.operations).toEqual([{ op: "replace_recipe_resources", resources: [] }]);
    expect(applied).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认应用修复" }));
    await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ ...body, preview_checksum: "d".repeat(64) });
  });

  it("rejects missing resources and unknown retention formats", () => {
    const raw = resourceDraftFixture();
    expect(normalizeRecoveryState({ ...raw, recovery: { ...raw.recovery, source_format: "unknown" } })?.can_author).toBe(false);
    expect(normalizeRecoveryState({ ...raw, recovery: { ...raw.recovery, recipe: { ...raw.recovery.recipe, resources: null } } })?.can_author).toBe(false);
  });
});

describe("失败 Recipe 的受限修复", () => {
  it("keeps Recipe distinct from Intent and never exposes paid patch repair", () => {
    const state = normalizeRecoveryState(recipeFixture())!;
    expect(state.can_author).toBe(true);
    expect(state.recovery.intent.nodes).toEqual([]);
    render(<FailedDraftRepair state={state} onApplied={vi.fn()} />);
    expect(screen.getByText("生成描述展开：失败")).toBeInTheDocument();
    expect(screen.getByText("首次出现：control_flow.1.node_ref")).toBeInTheDocument();
    expect(screen.getByLabelText("控制结构 JSON")).toBeInTheDocument();
    expect(screen.queryByLabelText("节点语义配置 JSON")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定向模型修复" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认应用修复" })).toBeDisabled();
  });

  it("fails closed on an incomplete Recipe projection", () => {
    const raw = recipeFixture();
    raw.recovery.recipe.control_flow = [];
    expect(normalizeRecoveryState(raw)?.can_author).toBe(false);
    expect(normalizeRecoveryState(raw)?.recovery.recipe).toBeUndefined();
  });

  it("previews only a typed control replacement and applies the identical request", async () => {
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify(body.preview_checksum ? { proposal_revision: 2 } : { preview_checksum: "d".repeat(64), can_apply: true, diagnostics: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const applied = vi.fn(async () => undefined);
    render(<FailedDraftRepair state={normalizeRecoveryState(recipeFixture())!} onApplied={applied} />);
    const flow = recipeFixture().recovery.recipe.control_flow.slice(0, 2);
    fireEvent.change(screen.getByLabelText("控制结构 JSON"), { target: { value: JSON.stringify(flow) } });
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    await screen.findByText("修复预览通过，尚未写回");
    const before = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(before.patch.protocol_version).toBe("recipe_control_flow_v1");
    expect(before.patch.operations).toEqual([{ op: "replace_recipe_control_flow", control_flow: flow }]);
    expect(applied).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认应用修复" }));
    await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ ...before, preview_checksum: "d".repeat(64) });
  });

  it("invalidates preview on edits and locks on conflict without retry", async () => {
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return body.preview_checksum ? new Response(JSON.stringify({ detail: { message: "资源已变化" } }), { status: 409 })
        : new Response(JSON.stringify({ preview_checksum: "d".repeat(64), can_apply: true, diagnostics: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<FailedDraftRepair state={normalizeRecoveryState(recipeFixture())!} onApplied={vi.fn()} />);
    const edit = (flow: unknown) => fireEvent.change(screen.getByLabelText("控制结构 JSON"), { target: { value: JSON.stringify(flow) } });
    edit(recipeFixture().recovery.recipe.control_flow.slice(0, 2));
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    await screen.findByText("修复预览通过，尚未写回");
    edit([{ type: "node", node_ref: "lookup" }]);
    expect(screen.getByRole("button", { name: "确认应用修复" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    await screen.findByText("修复预览通过，尚未写回");
    fireEvent.click(screen.getByRole("button", { name: "确认应用修复" }));
    await screen.findByText("资源已变化");
    expect(screen.getByLabelText("控制结构 JSON")).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe("失败候选人工修复", () => {
  it("loads a model suggestion locally and still requires separate preview and apply", async () => {
    const operations = [{ op: "connect_data", source_ref: "lookup", target_ref: "write", source_port: "result", target_port: "records" }];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      if (url.endsWith("/preflight")) return new Response(JSON.stringify({ ...body, can_dispatch: true, max_calls: 1,
        authorization_checksum: "d".repeat(64), authorization_token: "e".repeat(32) + ".9999999999." + "f".repeat(64), expires_at: 9999999999,
        route: { label: "离线模拟" }, outbound: { messages: [] } }));
      if (url.endsWith("/execute")) return new Response(JSON.stringify({ status: "suggested", request_id: body.request_id,
        patch: { protocol_version: 1, proposal_revision: 1, expected_graph_checksum: "a".repeat(64), expected_candidate_checksum: "b".repeat(64), operations } }));
      return new Response(JSON.stringify({ preview_checksum: "d".repeat(64), can_apply: true, diagnostics: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const applied = vi.fn();
    render(<FailedDraftRepair state={normalizeRecoveryState(recoveryFixture())!} onApplied={applied} />);
    fireEvent.click(screen.getByRole("button", { name: "定向模型修复" }));
    fireEvent.change(screen.getByLabelText("修复模型"), { target: { value: "test/repair" } });
    fireEvent.click(screen.getByRole("button", { name: "查看本次外发内容" }));
    fireEvent.click(await screen.findByLabelText(/我确认以上内容/));
    fireEvent.click(screen.getByRole("button", { name: "授权并执行一次修复" }));
    await screen.findByRole("button", { name: "载入人工编辑区" });
    expect(screen.getByLabelText("修复操作 JSON")).toHaveValue("[]");
    fireEvent.click(screen.getByRole("button", { name: "载入人工编辑区" }));
    expect(screen.getByLabelText("修复操作 JSON")).toHaveValue(JSON.stringify(operations, null, 2));
    expect(screen.getByRole("button", { name: "确认应用修复" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    await screen.findByText("修复预览通过，尚未写回");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(applied).not.toHaveBeenCalled();
  });

  it("rejects incomplete or executable projections without inventing a graph", () => {
    expect(normalizeRecoveryState({ mode: "other" })).toBeNull();
    const raw = recoveryFixture();
    raw.recovery.executable = true;
    expect(normalizeRecoveryState(raw)?.can_author).toBe(false);
    raw.recovery.executable = false;
    raw.candidate_checksum = "forged";
    expect(normalizeRecoveryState(raw)?.can_author).toBe(false);
  });

  it("locates real diagnostics and preserves blocked stages", () => {
    render(<FailedDraftRepair state={normalizeRecoveryState(recoveryFixture())!} onApplied={vi.fn()} />);
    expect(screen.getByText("编译：未执行")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "定位 write" }));
    expect(screen.getByLabelText("节点标题")).toHaveValue("更新记录");
    expect(screen.getByLabelText("节点语义配置 JSON")).toHaveValue(JSON.stringify({ values: { status: "需复核" } }, null, 2));
    expect(screen.getByRole("button", { name: "确认应用修复" })).toBeDisabled();
  });

  it("previews without saving and applies exactly the approved patch", async () => {
    const applied = vi.fn(async () => undefined);
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      expect(body.mode).toBe("recovery");
      expect(body.artifact_checksum).toBe("c".repeat(64));
      return new Response(JSON.stringify(body.preview_checksum ? { proposal_revision: 2 } : { preview_checksum: "d".repeat(64), can_apply: true, diagnostics: [], diff: { operation_count: 1 } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<FailedDraftRepair state={normalizeRecoveryState(recoveryFixture())!} onApplied={applied} />);
    fireEvent.click(screen.getByRole("button", { name: "加入连接修改" }));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    expect(await screen.findByText("修复预览通过，尚未写回")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(applied).not.toHaveBeenCalled();
    const first = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(first.patch.operations).toEqual([{ op: "connect_data", source_ref: "lookup", target_ref: "write", source_port: "result", target_port: "records" }]);
    fireEvent.click(screen.getByRole("button", { name: "确认应用修复" }));
    await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const second = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
    expect(second.patch).toEqual(first.patch);
    expect(second.preview_checksum).toBe("d".repeat(64));
    expect(screen.getByRole("button", { name: "确认应用修复" })).toBeDisabled();
  });

  it("invalidates a previous preview on every edit", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ preview_checksum: "d".repeat(64), can_apply: true, diagnostics: [] }))));
    render(<FailedDraftRepair state={normalizeRecoveryState(recoveryFixture())!} onApplied={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "加入连接修改" }));
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    await screen.findByText("修复预览通过，尚未写回");
    fireEvent.change(screen.getByLabelText("节点标题"), { target: { value: "新标题" } });
    expect(screen.getByRole("button", { name: "确认应用修复" })).toBeDisabled();
    expect(screen.queryByText("修复预览通过，尚未写回")).not.toBeInTheDocument();
  });

  it("retains local work but blocks retry after a revision conflict", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ detail: { message: "版本冲突，请重新加载" } }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<FailedDraftRepair state={normalizeRecoveryState(recoveryFixture())!} onApplied={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "加入连接修改" }));
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("版本冲突");
    expect((screen.getByLabelText("修复操作 JSON") as HTMLTextAreaElement).value).toContain("connect_data");
    expect(screen.getByRole("button", { name: "校验并预览修复" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refuses invalid local JSON before making any request", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    render(<FailedDraftRepair state={normalizeRecoveryState(recoveryFixture())!} onApplied={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("修复操作 JSON"), { target: { value: "{broken" } });
    fireEvent.click(screen.getByRole("button", { name: "校验并预览修复" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
