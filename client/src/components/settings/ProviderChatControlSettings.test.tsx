import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ProviderChatControlSettings from "./ProviderChatControlSettings";


const policy = {
  contract_version: "modelmirror-provider-chat-routing-v1",
  feature_enabled: false,
  data_plane_integrated: true,
  configured_mode: "legacy",
  effective_mode: "legacy",
  auto_enabled: false,
  revision: 0,
  policy_fingerprint: "fingerprint",
  stable_model_ids: [],
  routes: [
    { capability: "chat_text", connection_ids: [] },
    { capability: "chat_tools", connection_ids: [] },
    { capability: "chat_file_output", connection_ids: [] },
  ],
  qualifications: [],
};

const collectingGate = {
  feature_enabled: false,
  configured_mode: "legacy",
  ready: false,
  required_activation_available: false,
  required_active: false,
  epoch_status: "collecting",
  hard_failure_code: null,
  minimum_request_count: 500,
  minimum_observed_days: 14,
  minimum_success_rate: 0.99,
  request_count: 0,
  success_count: 0,
  hard_failure_count: 0,
  observed_days: 0,
  success_rate: null,
  model_progress: [],
  required_drills: ["auth_failure"],
  approval_recorded: false,
  acceptance_evidence_complete: false,
  acceptance_evidence: [],
  blocking_reason_codes: ["provider_chat_gate_request_count_insufficient"],
};

describe("ProviderChatControlSettings", () => {
  afterEach(() => vi.restoreAllMocks());

  it("describes the bounded R5E gate and saves an atomic revisioned policy", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      calls.push([input, init]);
      const url = String(input);
      if (url.endsWith("/chat-control/gate")) {
        return new Response(JSON.stringify(collectingGate));
      }
      if (url.endsWith("/connections")) {
        return new Response(
          JSON.stringify([
            {
              id: "conn-newapi",
              name: "newAPI",
              kind: "newapi",
              enabled: true,
              scopes: ["chat"],
            },
            {
              id: "conn-audio",
              name: "Audio",
              kind: "openai",
              enabled: true,
              scopes: ["audio"],
            },
          ]),
        );
      }
      if (init?.method === "PUT") {
        return new Response(
          JSON.stringify({
            ...policy,
            revision: 1,
            configured_mode: "newapi_preferred",
          }),
        );
      }
      return new Response(JSON.stringify(policy));
    });

    render(<ProviderChatControlSettings csrfToken="csrf-test" />);
    expect(await screen.findByText("R5E 资格证据与 required 门禁")).toBeInTheDocument();
    expect(screen.getByText(/真实普通文本样本、逐模型成功数和故障演练证据/)).toBeInTheDocument();
    expect(screen.getByText(/Auto、工具、文件和多模态不会计入/)).toBeInTheDocument();
    expect(screen.getByText(/正在收集资格证据/)).toBeInTheDocument();
    expect(
      (screen.getByRole("option", { name: /newapi_required_default/ }) as HTMLOptionElement)
        .disabled,
    ).toBe(true);

    fireEvent.change(screen.getByLabelText("租户策略模式"), {
      target: { value: "newapi_preferred" },
    });
    fireEvent.change(screen.getByLabelText("稳定模型允许列表"), {
      target: { value: "provider/model" },
    });
    fireEvent.click(screen.getByText("启用 Auto 独立证据管道（不改变选路）"));
    fireEvent.click(screen.getAllByRole("button", { name: "添加目标" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "原子保存策略" }));

    await waitFor(() => {
      expect(calls.some(([, init]) => init?.method === "PUT")).toBe(true);
    });
    const put = calls.find(([, init]) => init?.method === "PUT");
    const body = JSON.parse(String(put?.[1]?.body));
    expect(body).toMatchObject({
      expected_revision: 0,
      mode: "newapi_preferred",
      auto_enabled: true,
      stable_model_ids: ["provider/model"],
    });
    expect(body.routes[0]).toEqual({
      capability: "chat_text",
      connection_ids: ["conn-newapi"],
    });
    expect(put?.[1]?.headers).toMatchObject({
      "X-ModelMirror-CSRF": "csrf-test",
    });
  });

  it("requires every Go/No-Go acknowledgement before activating required", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const readyPolicy = {
      ...policy,
      feature_enabled: true,
      configured_mode: "newapi_preferred",
      effective_mode: "newapi_preferred",
      revision: 7,
      stable_model_ids: ["provider/model"],
    };
    const readyGate = {
      ...collectingGate,
      feature_enabled: true,
      configured_mode: "newapi_preferred",
      ready: true,
      required_activation_available: true,
      epoch_status: "ready",
      request_count: 500,
      success_count: 500,
      observed_days: 14,
      success_rate: 1,
      model_progress: [
        {
          model_id: "provider/model",
          success_count: 500,
          minimum_success_count: 10,
          ready: true,
        },
      ],
      blocking_reason_codes: [],
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      calls.push([input, init]);
      const url = String(input);
      if (init?.method === "POST") {
        return new Response(
          JSON.stringify({
            ...readyGate,
            required_activation_available: false,
            required_active: true,
            epoch_status: "active",
          }),
        );
      }
      if (url.endsWith("/chat-control/gate")) {
        return new Response(JSON.stringify(readyGate));
      }
      if (url.endsWith("/connections")) return new Response(JSON.stringify([]));
      return new Response(JSON.stringify(readyPolicy));
    });

    render(<ProviderChatControlSettings csrfToken="csrf-activate" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "开始 Go/No-Go" }),
    );
    const activateButton = screen.getByRole("button", {
      name: "激活 newAPI 强制默认",
    });
    expect(activateButton).toBeDisabled();

    fireEvent.click(screen.getByText("当前没有未解决 P0/P1"));
    fireEvent.click(screen.getByText("确认 required 的失败关闭语义"));
    fireEvent.click(screen.getByText("故障演练：401 / 403 认证失败"));
    fireEvent.click(screen.getByText("newAPI 额度扣减差额已核对"));
    fireEvent.click(screen.getByText("newAPI Token 用量日志已关联"));
    fireEvent.click(screen.getByText("newAPI 重启后持久化已验证"));
    fireEvent.change(screen.getByLabelText("newAPI 验收关联引用"), {
      target: { value: "acceptance-log-20260822" },
    });
    expect(activateButton).toBeEnabled();
    fireEvent.click(activateButton);

    await waitFor(() => {
      expect(calls.some(([, init]) => init?.method === "POST")).toBe(true);
    });
    const post = calls.find(([, init]) => init?.method === "POST");
    expect(String(post?.[0])).toContain(
      "/api/router/chat-control/gate/activate-required",
    );
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
      expected_revision: 7,
      no_open_p0_p1: true,
      acknowledge_fail_closed: true,
      drills: { auth_failure: true },
      newapi_correlation_reference: "acceptance-log-20260822",
      quota_decrement_verified: true,
      usage_log_verified: true,
      restart_persistence_verified: true,
    });
    expect(post?.[1]?.headers).toMatchObject({
      "X-ModelMirror-CSRF": "csrf-activate",
    });
  });
});
