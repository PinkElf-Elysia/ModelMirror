import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ModelDraftRepair from "./ModelDraftRepair";
import { normalizeRecoveryState } from "./FailedDraftRepair";
import { normalizeRecipeRepairContract, normalizeRecipeRepairPatch } from "./recipeRepair";

vi.mock("../../data/models", () => ({ models: [{ id: "test/repair", name: "测试模型" }] }));

function state() {
  return normalizeRecoveryState({ mode: "recovery", proposal_id: "p", proposal_revision: 1, can_author: true, can_approve: false,
    graph_checksum: "a".repeat(64), candidate_checksum: "b".repeat(64), recovery: { status: "editable", executable: false,
      artifact_checksum: "c".repeat(64), intent: { nodes: [{ ref: "agent", kind: "workflow_agent", title: "汇总", config: {} }] } } })!;
}
const patch = { protocol_version: 1, proposal_revision: 1, expected_graph_checksum: "a".repeat(64), expected_candidate_checksum: "b".repeat(64),
  operations: [{ op: "update_node", ref: "agent", title: "核验后汇总" }] };
const success = { request_id: "repair_" + "a".repeat(32), status: "suggested", model_id: "test/repair", automatically_applied: false, patch,
  receipt: { provider_dispatched: true, response_received: true, usage_verified: true, total_tokens: 123 }, preview_summary: { can_apply: true, diagnostics: [] } };

function prepareResponse(body: Record<string, unknown>, extra = {}) {
  return { ...body, can_dispatch: true, max_calls: 1, authorization_checksum: "d".repeat(64), authorization_token: "e".repeat(32) + ".9999999999." + "f".repeat(64),
    expires_at: 9999999999, uncertain_previous: 0, outbound: { model: "test/repair", messages: [{ role: "user", content: "已明确批准的合成目标与节点 Prompt" }] },
    route: { label: "离线测试网关" }, ...extra };
}
function mount(extra = {}) {
  const props = { state: state(), disabled: false, hasLocalChanges: false, onLoad: vi.fn(), onBusyChange: vi.fn(), onConflict: vi.fn(), ...extra };
  render(<ModelDraftRepair {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "定向模型修复" }));
  return props;
}
async function prepare() {
  fireEvent.change(screen.getByLabelText("修复模型"), { target: { value: "test/repair" } });
  fireEvent.click(screen.getByRole("button", { name: "查看本次外发内容" }));
  await screen.findByRole("button", { name: "授权并执行一次修复" });
}
afterEach(() => vi.unstubAllGlobals());

describe("语义建议的协议边界", () => {
  const contract = normalizeRecipeRepairContract({ protocol_version: "recipe_edits_v1", operations_schema: { type: "object" }, edit_contract: {
    max_operations: 16, max_bytes: 65536, update_node_refs: ["agent"], cloneable_agent_refs: ["agent"], operations: {
      update_node: { required_fields: ["op", "node_ref"], optional_fields: ["title", "config", "inputs"] },
      clone_agent: { required_fields: ["op", "source_ref", "ref", "title", "task_input"], optional_fields: [] },
      replace_control_flow: { required_fields: ["op", "control_flow"], optional_fields: [] },
      set_final_output: { required_fields: ["op", "final_output"], optional_fields: [] },
    },
  } })!;
  const semantic = { ...patch, protocol_version: "recipe_edits_v1", operations: [{ op: "update_node", node_ref: "agent", title: "核对后汇总" }] };

  it("requires explicit complete server metadata", () => {
    expect(normalizeRecipeRepairContract(undefined)).toBeUndefined();
    expect(normalizeRecipeRepairContract({ ...contract, edit_contract: { ...contract.edit_contract, max_operations: 17 } })).toBeUndefined();
    expect(normalizeRecipeRepairContract({ ...contract, edit_contract: { ...contract.edit_contract, operations: {} } })).toBeUndefined();
    expect(normalizeRecipeRepairPatch(semantic, contract)?.operations).toEqual(semantic.operations);
  });
  it.each([
    { protocol_version: 1 }, { expected_candidate_checksum: "forged" }, { runtime_policy: {} },
    { operations: [{ op: "replace_control_flow", config: {} }] },
    { operations: [{ op: "update_node", title: "缺少引用" }] },
    { operations: Array.from({ length: 17 }, () => semantic.operations[0]) },
    { operations: [{ ...semantic.operations[0], title: "x".repeat(65536) }] },
  ])("rejects mismatched or unbounded suggestion %j", changes => {
    expect(normalizeRecipeRepairPatch({ ...semantic, ...changes }, contract)).toBeNull();
  });
  it("cannot load an old Graph Patch as a Recipe suggestion", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(
      url.endsWith("/preflight") ? prepareResponse(JSON.parse(String(init?.body)), { repair_protocol: "recipe_edits_v1" }) : success))));
    const base = state();
    const props = mount({ state: { ...base, recovery: { ...base.recovery, source_format: "recipe_v1", semantic_repair: contract } } });
    await prepare();
    fireEvent.click(screen.getByLabelText(/我确认以上内容/));
    fireEvent.click(screen.getByRole("button", { name: "授权并执行一次修复" }));
    expect(await screen.findByRole("button", { name: "载入人工编辑区" })).toBeDisabled();
    expect(props.onLoad).not.toHaveBeenCalled();
  });
});

describe("显式定向模型修复", () => {
  it.each([
    ["generation_recipe_v1", "语义描述，服务端生成修改"],
    ["recipe_edits_v1", "受限语义修改，未修改部分保留"],
  ])("shows %s without authorizing or dispatching it", async (protocol, label) => {
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(
      prepareResponse(JSON.parse(String(init?.body)), { repair_protocol: protocol }))));
    vi.stubGlobal("fetch", fetcher);
    mount();
    await prepare();
    expect(screen.getByText(`修复协议：${label}`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "授权并执行一次修复" })).toBeDisabled();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("does not call on mount or preflight, then only loads on explicit action", async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(
      url.endsWith("/preflight") ? prepareResponse(JSON.parse(String(init?.body))) : success)));
    vi.stubGlobal("fetch", fetcher);
    const props = mount();
    expect(fetcher).not.toHaveBeenCalled();
    await prepare();
    const execute = screen.getByRole("button", { name: "授权并执行一次修复" });
    expect(execute).toBeDisabled();
    expect(screen.getByLabelText(/我确认以上内容/)).not.toBeChecked();
    expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText(/我确认以上内容/));
    fireEvent.click(execute);
    await screen.findByRole("button", { name: "载入人工编辑区" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(props.onLoad).not.toHaveBeenCalled();
    const sent = JSON.parse(String(fetcher.mock.calls[1][1]?.body));
    expect(sent).toMatchObject({ max_calls: 1, acknowledge_external_send: true, model_id: "test/repair" });
    expect(sent.request_id).toMatch(/^repair_[a-f0-9]{32}$/);
    expect(screen.queryByRole("button", { name: "授权并执行一次修复" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "载入人工编辑区" }));
    expect(props.onLoad).toHaveBeenCalledWith(patch.operations);
    expect(fetcher.mock.calls.every(call => !String(call[0]).includes("/patch/apply"))).toBe(true);
  });

  it("invalidates confirmation after model direction or budget changes", async () => {
    const fetcher = vi.fn(async (_: unknown, init?: RequestInit) => new Response(JSON.stringify(prepareResponse(JSON.parse(String(init?.body))))));
    vi.stubGlobal("fetch", fetcher); mount(); await prepare();
    fireEvent.click(screen.getByLabelText(/我确认以上内容/));
    fireEvent.change(screen.getByLabelText("补充修复要求"), { target: { value: "保留分支" } });
    expect(screen.queryByRole("button", { name: "授权并执行一次修复" })).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("requires a second explicit acknowledgement after an uncertain call", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_: unknown, init?: RequestInit) => new Response(JSON.stringify(prepareResponse(JSON.parse(String(init?.body)), { uncertain_previous: 1 })))));
    mount(); await prepare(); fireEvent.click(screen.getByLabelText(/我确认以上内容/));
    expect(screen.getByRole("button", { name: "授权并执行一次修复" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/我理解新调用可能再次计费/));
    expect(screen.getByRole("button", { name: "授权并执行一次修复" })).toBeEnabled();
  });

  it("never replaces existing manual work", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(url.endsWith("/preflight") ? prepareResponse(JSON.parse(String(init?.body))) : success))));
    const props = mount({ hasLocalChanges: true }); await prepare();
    fireEvent.click(screen.getByLabelText(/我确认以上内容/)); fireEvent.click(screen.getByRole("button", { name: "授权并执行一次修复" }));
    expect(await screen.findByRole("button", { name: "载入人工编辑区" })).toBeDisabled();
    expect(props.onLoad).not.toHaveBeenCalled();
  });

  it("does not retry an interrupted paid request and can read its receipt", async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/preflight")) return new Response(JSON.stringify(prepareResponse(JSON.parse(String(init?.body)))));
      if (url.endsWith("/execute")) throw new TypeError("Failed to fetch");
      return new Response(JSON.stringify({ attempts: [{ ...success, status: "uncertain" }] }));
    });
    vi.stubGlobal("fetch", fetcher); mount(); await prepare();
    fireEvent.click(screen.getByLabelText(/我确认以上内容/)); fireEvent.click(screen.getByRole("button", { name: "授权并执行一次修复" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("不会自动重发");
    expect(fetcher).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "刷新调用回执" }));
    expect(await screen.findByText(/结果不确定，禁止自动重发/)).toBeInTheDocument();
    expect(fetcher.mock.calls.filter(call => String(call[0]).endsWith("/execute"))).toHaveLength(1);
  });

  it("blocks malformed confirmation", async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(url.endsWith("/preflight") ? prepareResponse(JSON.parse(String(init?.body)), { max_calls: 2 }) : { ...success, stale: true })));
    vi.stubGlobal("fetch", fetcher); const props = mount();
    fireEvent.change(screen.getByLabelText("修复模型"), { target: { value: "test/repair" } });
    fireEvent.click(screen.getByRole("button", { name: "查看本次外发内容" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("预检响应不完整");
    expect(screen.queryByLabelText(/我确认以上内容/)).not.toBeInTheDocument();
    expect(props.onLoad).not.toHaveBeenCalled();
  });

  it("cannot load a stale suggestion even if its patch looks valid", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(url.endsWith("/preflight") ? prepareResponse(JSON.parse(String(init?.body))) : { ...success, stale: true }))));
    const props = mount(); await prepare();
    fireEvent.click(screen.getByLabelText(/我确认以上内容/)); fireEvent.click(screen.getByRole("button", { name: "授权并执行一次修复" }));
    expect(await screen.findByRole("button", { name: "载入人工编辑区" })).toBeDisabled();
    expect(screen.getByText("依据已变化，建议不可载入")).toBeInTheDocument();
    expect(props.onLoad).not.toHaveBeenCalled();
  });

  it("shows the bounded cause and consumed receipt for a model mismatch", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(url.endsWith("/preflight") ? prepareResponse(JSON.parse(String(init?.body))) : {
      ...success, patch: undefined, status: "failed", reason_code: "repair_response_model_mismatch",
    }))));
    mount(); await prepare();
    fireEvent.click(screen.getByLabelText(/我确认以上内容/)); fireEvent.click(screen.getByRole("button", { name: "授权并执行一次修复" }));
    expect(await screen.findByText(/返回模型与本次确认不一致/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "载入人工编辑区" })).toBeDisabled();
  });

  it("distinguishes a patch representation limit without retrying or loading", async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(url.endsWith("/preflight")
      ? prepareResponse(JSON.parse(String(init?.body)))
      : { ...success, patch: undefined, status: "invalid", reason_code: "repair_patch_unrepresentable" })));
    vi.stubGlobal("fetch", fetcher);
    const props = mount(); await prepare();
    fireEvent.click(screen.getByLabelText(/我确认以上内容/)); fireEvent.click(screen.getByRole("button", { name: "授权并执行一次修复" }));
    expect(await screen.findByText(/修改超出单次 Patch 的 64 项操作上限/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "载入人工编辑区" })).toBeDisabled();
    expect(props.onLoad).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("reports conflict without retry or auto-apply", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: { message: "版本已变化" } }), { status: 409 })));
    const props = mount(); fireEvent.change(screen.getByLabelText("修复模型"), { target: { value: "test/repair" } });
    fireEvent.click(screen.getByRole("button", { name: "查看本次外发内容" }));
    await waitFor(() => expect(props.onConflict).toHaveBeenCalledTimes(1));
    expect(props.onLoad).not.toHaveBeenCalled();
  });
});
