import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ProviderWorkloadControlSettings, {
  ENTRY_SHAPES,
} from "./ProviderWorkloadControlSettings";

const connection = {
  id: "connection-openrouter",
  name: "OpenRouter managed",
  kind: "openrouter",
  scopes: ["chat"],
  enabled: true,
};

function jsonResponse(payload: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => payload,
  } as Response;
}

describe("ProviderWorkloadControlSettings", () => {
  it("binds Expert Team Planner to its unary YAML contract", () => {
    expect(ENTRY_SHAPES.expert_team_planner).toEqual(["chat_text_unary"]);
  });

  it("requires both unary text and JSON qualifications for Expert Team DAG", () => {
    expect(ENTRY_SHAPES.expert_team_dag).toEqual([
      "chat_text_unary",
      "chat_json_object",
    ]);
  });

  it("keeps R7 retrieval and batch operations explicit and uncombined", () => {
    expect(ENTRY_SHAPES.rag_embedding).toEqual(["embedding_vectors"]);
    expect(ENTRY_SHAPES.rag_rerank).toEqual(["rerank_documents"]);
    expect(ENTRY_SHAPES.skill_rerank).toEqual(["rerank_documents"]);
    expect(ENTRY_SHAPES.openrouter_batch).toEqual([
      "openrouter_batch_chat",
      "openrouter_batch_embeddings",
    ]);
  });

  it("keeps each R8 multimodal entry bound to one explicit protocol shape", () => {
    expect(ENTRY_SHAPES.chat_image).toEqual(["chat_image_stream"]);
    expect(ENTRY_SHAPES.chat_document_native).toEqual(["chat_document_stream"]);
    expect(ENTRY_SHAPES.multimodal_transcription).toEqual(["audio_transcription"]);
    expect(ENTRY_SHAPES.video_generation).toEqual(["video_generation_async"]);
    expect(ENTRY_SHAPES.realtime_voice).toEqual(["realtime_voice_session"]);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("requires an explicit confirmation before one billed workload certification", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/router/connections") return jsonResponse([connection]);
      if (url === "/api/router/certifications/workloads" && !init) {
        return jsonResponse({ certifications: [] });
      }
      if (url.includes("/certifications/workloads") && init?.method === "POST") {
        return jsonResponse({
          certification_id: "cert-1",
          connection_id: connection.id,
          connection_name: connection.name,
          provider_kind: connection.kind,
          execution_shape: "chat_json_object",
          status: "passed",
          can_run: true,
          candidate_model_ids: [],
          requested_model: "openai/gpt-test",
          total_tokens: 8,
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProviderWorkloadControlSettings csrfToken="csrf-value" view="certifications" />);
    await screen.findByText("R6 / R7 资格与 R8 多模态认证");
    fireEvent.change(screen.getByLabelText("执行形态"), {
      target: { value: "chat_json_object" },
    });
    fireEvent.change(screen.getByLabelText("精确模型 ID"), {
      target: { value: "openai/gpt-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "运行资格认证" }));

    expect(screen.getByRole("dialog")).toHaveTextContent("最多一个 Provider POST");
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/connections/connection-openrouter/certifications/workloads"),
      expect.anything(),
    );
    fireEvent.click(screen.getByRole("button", { name: "确认并运行" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/router/connections/connection-openrouter/certifications/workloads",
      expect.objectContaining({ method: "POST" }),
    ));
    const call = fetchMock.mock.calls.find(([url, options]) =>
      String(url).includes("connection-openrouter/certifications/workloads") && options?.method === "POST"
    );
    expect(call).toBeDefined();
    expect(call?.[1]?.headers).toEqual(expect.objectContaining({
      "X-ModelMirror-CSRF": "csrf-value",
    }));
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      execution_shape: "chat_json_object",
      model_id: "openai/gpt-test",
      acknowledge_billed_call: true,
    });
  });

  it("opens R8B image certification while later multimodal shapes remain blocked", async () => {
    const multimodalConnection = {
      ...connection,
      scopes: ["chat", "image", "audio"],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/router/connections") return jsonResponse([multimodalConnection]);
      if (url === "/api/router/certifications/workloads" && !init) {
        return jsonResponse({ certifications: [] });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProviderWorkloadControlSettings csrfToken="csrf-value" view="certifications" />);
    await screen.findByText("R6 / R7 资格与 R8 多模态认证");
    fireEvent.change(screen.getByLabelText("执行形态"), {
      target: { value: "chat_image_stream" },
    });
    fireEvent.change(screen.getByLabelText("精确模型 ID"), {
      target: { value: "openai/gpt-4o-mini" },
    });

    await waitFor(() => expect(screen.getByRole("button", {
      name: "运行资格认证",
    })).toBeEnabled());
    expect(screen.getByLabelText("Adapter Contract")).toHaveValue(
      "openrouter_chat_multimodal_v1",
    );
    fireEvent.click(screen.getByRole("button", { name: "运行资格认证" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("最多一个 Provider POST");
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/certifications/workloads"),
      expect.objectContaining({ method: "POST" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    fireEvent.change(screen.getByLabelText("执行形态"), {
      target: { value: "audio_transcription" },
    });
    await waitFor(() => expect(screen.getByRole("button", {
      name: "运行资格认证",
    })).toBeDisabled());
    expect(screen.getByText(/该多模态形态目前仅建立 Adapter/)).toBeVisible();
  });

  it("saves exact bindings with optimistic revision while an unintegrated entry stays blocked", async () => {
    const policy = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "agent_shadow",
      feature_enabled: false,
      data_plane_integrated: false,
      configured_status: "legacy",
      effective_status: "legacy",
      revision: 3,
      policy_fingerprint: "fingerprint",
      bindings: [],
      approval_valid: false,
      blocking_reason_codes: ["provider_workload_data_plane_not_integrated"],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/router/workload-control/policies" && !init) {
        return jsonResponse({ policies: [policy] });
      }
      if (url === "/api/router/connections") return jsonResponse([connection]);
      if (url.startsWith("/api/router/workload-control/receipts")) {
        return jsonResponse({ runs: [] });
      }
      if (url === "/api/router/workload-control/policies/agent_shadow" && init?.method === "PUT") {
        return jsonResponse({ ...policy, revision: 4 });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProviderWorkloadControlSettings csrfToken="csrf-value" view="routing" />);
    await screen.findByText("R6 / R7 入口与 R8 多模态控制面基础");
    expect(screen.getByRole("button", { name: /激活 Managed/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "添加 Binding" }));
    fireEvent.change(screen.getByLabelText("Binding 1 模型 ID"), {
      target: { value: "openai/gpt-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存 Binding" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/router/workload-control/policies/agent_shadow",
      expect.objectContaining({ method: "PUT" }),
    ));
    const call = fetchMock.mock.calls.find(([url, options]) =>
      url === "/api/router/workload-control/policies/agent_shadow" && options?.method === "PUT"
    );
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      expected_revision: 3,
      local_fallback_mode: "none",
      bindings: [{
        execution_shape: "chat_tools",
        model_id: "openai/gpt-test",
        connection_id: connection.id,
      }],
    });
  });

  it("persists the explicitly selected Rerank access mode in a binding", async () => {
    const rerankConnection = {
      ...connection,
      id: "connection-rerank",
      name: "Rerank managed",
      scopes: ["rerank"],
    };
    const policy = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "rag_rerank",
      feature_enabled: false,
      data_plane_integrated: false,
      configured_status: "legacy",
      effective_status: "legacy",
      revision: 2,
      policy_fingerprint: "rerank-fingerprint",
      local_fallback_mode: "none",
      bindings: [],
      approval_valid: false,
      blocking_reason_codes: ["provider_workload_data_plane_not_integrated"],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/router/workload-control/policies" && !init) {
        return jsonResponse({ policies: [policy] });
      }
      if (url === "/api/router/connections") return jsonResponse([rerankConnection]);
      if (url.startsWith("/api/router/workload-control/receipts")) {
        return jsonResponse({ runs: [] });
      }
      if (url === "/api/router/workload-control/policies/rag_rerank" && init?.method === "PUT") {
        return jsonResponse({ ...policy, revision: 3 });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProviderWorkloadControlSettings csrfToken="csrf-value" view="routing" />);
    await screen.findByText("R6 / R7 入口与 R8 多模态控制面基础");
    fireEvent.change(screen.getByLabelText("入口"), {
      target: { value: "rag_rerank" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加 Binding" }));
    fireEvent.change(screen.getByLabelText("Binding 1 Rerank 访问方式"), {
      target: { value: "llm_json" },
    });
    fireEvent.change(screen.getByLabelText("Binding 1 模型 ID"), {
      target: { value: "provider/rerank" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存 Binding" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/router/workload-control/policies/rag_rerank",
      expect.objectContaining({ method: "PUT" }),
    ));
    const call = fetchMock.mock.calls.find(([url, options]) =>
      url === "/api/router/workload-control/policies/rag_rerank"
      && options?.method === "PUT"
    );
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      expected_revision: 2,
      bindings: [{
        execution_shape: "rerank_documents",
        model_id: "provider/rerank",
        connection_id: rerankConnection.id,
        rerank_access_mode: "llm_json",
      }],
    });
  });

  it("never initializes an OpenRouter Batch binding with another provider kind", async () => {
    const newApiBatchConnection = {
      ...connection,
      id: "connection-newapi-batch",
      name: "newAPI Batch",
      kind: "newapi",
      scopes: ["batch"],
    };
    const openRouterBatchConnection = {
      ...connection,
      id: "connection-openrouter-batch",
      name: "OpenRouter Batch",
      scopes: ["batch"],
    };
    const policy = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "openrouter_batch",
      feature_enabled: false,
      data_plane_integrated: false,
      configured_status: "legacy",
      effective_status: "legacy",
      revision: 0,
      policy_fingerprint: "batch-fingerprint",
      local_fallback_mode: "none",
      bindings: [],
      approval_valid: false,
      blocking_reason_codes: ["provider_workload_data_plane_not_integrated"],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/router/workload-control/policies") {
        return jsonResponse({ policies: [policy] });
      }
      if (url === "/api/router/connections") {
        return jsonResponse([newApiBatchConnection, openRouterBatchConnection]);
      }
      if (url.startsWith("/api/router/workload-control/receipts")) {
        return jsonResponse({ runs: [] });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProviderWorkloadControlSettings csrfToken="csrf-value" view="routing" />);
    await screen.findByText("R6 / R7 入口与 R8 多模态控制面基础");
    fireEvent.change(screen.getByLabelText("入口"), {
      target: { value: "openrouter_batch" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加 Binding" }));

    expect(screen.getByLabelText("Binding 1 连接")).toHaveValue(
      openRouterBatchConnection.id,
    );
    expect(screen.queryByRole("option", { name: newApiBatchConnection.name })).not.toBeInTheDocument();
  });

  it("persists an explicit R8 Adapter while keeping the data-plane activation blocked", async () => {
    const imageConnection = {
      ...connection,
      id: "connection-image",
      name: "OpenRouter image",
      scopes: ["chat", "image"],
    };
    const policy = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "chat_image",
      feature_enabled: false,
      data_plane_integrated: false,
      configured_status: "legacy",
      effective_status: "legacy",
      revision: 0,
      policy_fingerprint: "r8a-image-policy",
      local_fallback_mode: "none",
      bindings: [],
      approval_valid: false,
      blocking_reason_codes: ["provider_workload_data_plane_not_integrated"],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/router/workload-control/policies" && !init) return jsonResponse({ policies: [policy] });
      if (url === "/api/router/connections") return jsonResponse([imageConnection]);
      if (url.startsWith("/api/router/workload-control/receipts")) return jsonResponse({ runs: [] });
      if (url === "/api/router/workload-control/policies/chat_image" && init?.method === "PUT") return jsonResponse({ ...policy, revision: 1 });
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProviderWorkloadControlSettings csrfToken="csrf-value" view="routing" />);
    await screen.findByText("R6 / R7 入口与 R8 多模态控制面基础");
    fireEvent.change(screen.getByLabelText("入口"), { target: { value: "chat_image" } });
    fireEvent.click(screen.getByRole("button", { name: "添加 Binding" }));
    expect(screen.getByLabelText("Binding 1 Adapter")).toHaveValue(
      "openrouter_chat_multimodal_v1",
    );
    expect(screen.getByLabelText("Binding 1 连接")).toHaveValue(imageConnection.id);
    fireEvent.change(screen.getByLabelText("Binding 1 模型 ID"), {
      target: { value: "provider/vision" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存 Binding" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/router/workload-control/policies/chat_image",
      expect.objectContaining({ method: "PUT" }),
    ));
    const call = fetchMock.mock.calls.find(([url, options]) =>
      url === "/api/router/workload-control/policies/chat_image" && options?.method === "PUT"
    );
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      bindings: [{
        execution_shape: "chat_image_stream",
        model_id: "provider/vision",
        connection_id: imageConnection.id,
        adapter_contract: "openrouter_chat_multimodal_v1",
      }],
    });
    expect(screen.getByRole("button", { name: /激活 Managed/ })).toBeDisabled();
  });

  it("requires both operator acknowledgements before activating an integrated entry", async () => {
    const policy = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "agent_shadow",
      feature_enabled: true,
      data_plane_integrated: true,
      configured_status: "legacy",
      effective_status: "legacy",
      revision: 4,
      policy_fingerprint: "fingerprint",
      bindings: [{
        execution_shape: "chat_tools",
        model_id: "openai/gpt-test",
        connection_id: connection.id,
        connection_name: connection.name,
        provider_kind: connection.kind,
        certification_id: "cert-chat-tools",
        valid: true,
        reason_code: "qualified",
      }],
      approval_valid: false,
      blocking_reason_codes: [],
    };
    let activated = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/router/workload-control/policies" && !init) {
        return jsonResponse({
          policies: [activated ? {
            ...policy,
            configured_status: "managed_required",
            effective_status: "managed_required",
            revision: 5,
            approval_valid: true,
          } : policy],
        });
      }
      if (url === "/api/router/connections") return jsonResponse([connection]);
      if (url.startsWith("/api/router/workload-control/receipts")) {
        return jsonResponse({ runs: [] });
      }
      if (
        url === "/api/router/workload-control/policies/agent_shadow/activate"
        && init?.method === "POST"
      ) {
        activated = true;
        return jsonResponse({ ...policy, configured_status: "managed_required" });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProviderWorkloadControlSettings csrfToken="csrf-value" view="routing" />);
    await screen.findByText("R6 / R7 入口与 R8 多模态控制面基础");
    const activateButton = screen.getByRole("button", { name: "激活 Managed 必经" });
    expect(activateButton).toBeEnabled();
    fireEvent.click(activateButton);

    const confirmButton = screen.getByRole("button", { name: "确认激活" });
    expect(confirmButton).toBeDisabled();
    fireEvent.click(screen.getByLabelText("确认当前没有未解决的 P0/P1 阻塞项"));
    expect(confirmButton).toBeDisabled();
    fireEvent.click(screen.getByLabelText("理解并接受 Managed 不可用时失败关闭"));
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/router/workload-control/policies/agent_shadow/activate",
      expect.objectContaining({ method: "POST" }),
    ));
    const call = fetchMock.mock.calls.find(([url, options]) =>
      url === "/api/router/workload-control/policies/agent_shadow/activate"
      && options?.method === "POST"
    );
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      expected_revision: 4,
      no_open_p0_p1: true,
      acknowledge_fail_closed: true,
    });
    await screen.findByText("Managed 必经");
  });

  it("allows a degraded managed entry to be explicitly re-approved", async () => {
    const policy = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "expert_team_planner",
      feature_enabled: true,
      data_plane_integrated: true,
      configured_status: "managed_required",
      effective_status: "degraded_required",
      revision: 3,
      policy_fingerprint: "fingerprint",
      bindings: [{
        execution_shape: "chat_text_unary",
        model_id: "openai/gpt-test",
        connection_id: connection.id,
        connection_name: connection.name,
        provider_kind: connection.kind,
        certification_id: "cert-unary",
        valid: true,
        reason_code: "qualified",
      }],
      approval_valid: false,
      blocking_reason_codes: [],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/router/workload-control/policies") {
        return jsonResponse({ policies: [policy] });
      }
      if (url === "/api/router/connections") return jsonResponse([connection]);
      if (url.startsWith("/api/router/workload-control/receipts")) {
        return jsonResponse({ runs: [] });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProviderWorkloadControlSettings csrfToken="csrf-value" view="routing" />);
    await screen.findByText("R6 / R7 入口与 R8 多模态控制面基础");
    fireEvent.change(screen.getByLabelText("入口"), {
      target: { value: "expert_team_planner" },
    });
    expect(await screen.findByRole("button", { name: "重新批准 Managed 必经" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "显式恢复 Legacy" })).toBeEnabled();
  });

  it("keeps connection and call evidence in the authenticated settings view", async () => {
    const policy = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "workflow_interactive_llm",
      feature_enabled: true,
      data_plane_integrated: true,
      configured_status: "managed_required",
      effective_status: "managed_required",
      revision: 2,
      policy_fingerprint: "fingerprint",
      bindings: [],
      approval_valid: true,
      blocking_reason_codes: [],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/router/workload-control/policies") {
        return jsonResponse({ policies: [policy] });
      }
      if (url === "/api/router/connections") return jsonResponse([connection]);
      if (url.startsWith("/api/router/workload-control/receipts")) {
        return jsonResponse({ runs: [{
          run_id: "workrun-1",
          entry_id: "workflow_interactive_llm",
          status: "passed",
          parent_run_reference: "interactive:task-1:node-1",
          result_class: "workflow_node_passed",
          reason_codes: [],
          created_at: "2026-08-23T00:00:00Z",
          calls: [{
            call_id: "call-1",
            execution_shape: "chat_text",
            model_id: "openai/gpt-test",
            actual_model: "openai/gpt-test",
            connection_id: "connection-internal-ref",
            call_sequence: 1,
            dispatched: true,
            status: "passed",
            result_class: "provider_workload_success",
            ttft_ms: 125,
            e2e_ms: 480,
            total_tokens: 17,
          }],
        }] });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProviderWorkloadControlSettings csrfToken="csrf-value" view="routing" />);
    await screen.findByText("R6 / R7 入口与 R8 多模态控制面基础");
    fireEvent.click(screen.getByText("Workflow 交互 LLM · passed"));

    expect(screen.getByText("请求模型：openai/gpt-test")).toBeInTheDocument();
    expect(screen.getByText("连接引用：connection-internal-ref")).toBeInTheDocument();
    expect(screen.getByText("TTFT 125 ms")).toBeInTheDocument();
    expect(screen.getByText("17 tokens")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("private prompt");
    expect(document.body.textContent).not.toContain("api_key");
  });

  it("shows local Batch status without exposing an upstream Batch id", async () => {
    const policy = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "openrouter_batch",
      feature_enabled: true,
      data_plane_integrated: true,
      configured_status: "managed_required",
      effective_status: "managed_required",
      revision: 2,
      policy_fingerprint: "batch-policy",
      local_fallback_mode: "none",
      bindings: [],
      approval_valid: true,
      blocking_reason_codes: [],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/router/workload-control/policies") {
        return jsonResponse({ policies: [policy] });
      }
      if (url === "/api/router/connections") return jsonResponse([connection]);
      if (url.startsWith("/api/router/workload-control/receipts")) {
        return jsonResponse({ runs: [{
          run_id: "workrun-batch",
          entry_id: "openrouter_batch",
          status: "passed",
          result_class: "batch_submitted",
          reason_codes: [],
          created_at: "2026-08-26T00:00:00Z",
          batch_job_id: "mmbatch_0123456789abcdef0123456789abcdef",
          batch_status: "in_progress",
          batch_request_count: 3,
          batch_completed_count: 1,
          batch_failed_count: 0,
          billing_authoritative: false,
          calls: [],
        }] });
      }
      throw new Error(`Unexpected fetch ${url}`);
    }));

    render(<ProviderWorkloadControlSettings csrfToken="csrf-value" view="routing" />);
    await screen.findByText("R6 / R7 入口与 R8 多模态控制面基础");
    fireEvent.change(screen.getByLabelText("入口"), {
      target: { value: "openrouter_batch" },
    });
    fireEvent.click(screen.getByText("OpenRouter Batch · passed"));

    expect(screen.getByText(/mmbatch_0123456789abcdef/)).toHaveTextContent(
      "in_progress · 1 / 3",
    );
    expect(screen.getByText(/不构成 ModelMirror 计费依据/)).toBeVisible();
    expect(document.body.textContent).not.toContain("batch_upstream_secret");
  });
});
