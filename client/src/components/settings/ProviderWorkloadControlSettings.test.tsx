import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ProviderWorkloadControlSettings from "./ProviderWorkloadControlSettings";

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
    await screen.findByText("R6 非流式、JSON 与原生 Fusion 合同");
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
    await screen.findByText("R6 入口、精确 Binding 与 Receipt");
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
      bindings: [{
        execution_shape: "chat_tools",
        model_id: "openai/gpt-test",
        connection_id: connection.id,
      }],
    });
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
    await screen.findByText("R6 入口、精确 Binding 与 Receipt");
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
});
