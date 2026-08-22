import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NewApiChatCertification from "./NewApiChatCertification";

function response(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("NewApiChatCertification", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requires refresh, model selection, and billed-call confirmation before one POST", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/canaries/chat")) {
        return response({
          contract_version: "modelmirror-provider-chat-canary-v1",
          feature_enabled: true,
          policy_enabled: false,
          selected_connection_id: null,
          consent_revision: "provider-chat-canary-consent-v1",
          connections: [],
          runs: [],
        });
      }
      if (url.includes("/models/refresh")) {
        return response({
          ok: true,
          model_ids: ["provider/model"],
          model_count: 1,
          checked_at: "2026-08-20T00:00:00Z",
          truncated: false,
          message: "ok",
        });
      }
      if (url.includes("/connections/conn-1/certifications/chat")) {
        expect(init?.method).toBe("POST");
        return response({
          certification_id: "cert-1",
          connection_id: "conn-1",
          status: "passed",
          can_run: true,
          warning_codes: [],
        });
      }
      return response({
          enabled: true,
          contract_version: "modelmirror-provider-chat-v1",
          certifications: [
            {
              connection_id: "conn-1",
              capability: "chat_text",
              status: "not_run",
              can_run: true,
              warning_codes: [],
            },
          ],
        });
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "idempotency-1" });

    render(
      <NewApiChatCertification
        connectionEnabled
        connectionId="conn-1"
        connectionKind="newapi"
        csrfToken="csrf-1"
      />,
    );

    expect(
      screen.getAllByText("不代表默认数据面已就绪", { exact: false }),
    ).toHaveLength(2);
    expect(screen.getByRole("button", { name: "运行能力认证" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "刷新可认证模型" }));
    await screen.findByRole("option", { name: "provider/model" });
    fireEvent.click(screen.getByRole("button", { name: "运行能力认证" }));

    expect(screen.getByRole("dialog")).toHaveTextContent("最多一次真实 Chat 请求");
    expect(screen.getByRole("dialog")).toHaveTextContent("max_tokens=64");
    expect(screen.getByRole("dialog")).toHaveTextContent("可能产生少量额度费用");
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).includes("/certifications/chat"),
      ).filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "确认并运行一次" }));
    await waitFor(() =>
      expect(screen.getAllByText("当前能力已通过")).toHaveLength(2),
    );

    const certificationCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).includes("/connections/conn-1/certifications/chat") &&
        init?.method === "POST",
    );
    expect(certificationCall).toBeDefined();
    if (!certificationCall) throw new Error("certification call missing");
    expect(certificationCall[0]).toContain("/certifications/chat");
    expect(certificationCall[1]).toMatchObject({
      method: "POST",
      headers: expect.objectContaining({
        "Idempotency-Key": "idempotency-1",
        "X-ModelMirror-CSRF": "csrf-1",
      }),
    });
    expect(JSON.parse(String(certificationCall[1]?.body))).toEqual({
      model_id: "provider/model",
      capability: "chat_text",
      acknowledge_billed_call: true,
    });
  });

  it("shows disabled state without offering a certification call", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        String(input).includes("/canaries/chat")
          ? response({
              contract_version: "modelmirror-provider-chat-canary-v1",
              feature_enabled: false,
              policy_enabled: false,
              selected_connection_id: null,
              consent_revision: "provider-chat-canary-consent-v1",
              connections: [],
              runs: [],
            })
          : response({
          enabled: false,
          contract_version: "modelmirror-provider-chat-v1",
          certifications: [
            {
              connection_id: "conn-1",
              capability: "chat_text",
              status: "not_run",
              can_run: false,
              blocked_reason: "provider_chat_certification_disabled",
              warning_codes: [],
            },
          ],
            }),
      ),
    );

    render(
      <NewApiChatCertification
        connectionEnabled
        connectionId="conn-1"
        connectionKind="newapi"
        csrfToken="csrf-1"
      />,
    );

    await waitFor(() =>
      expect(screen.getByText("部署已关闭 Chat 认证操作。")).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "运行能力认证" })).toBeDisabled();
  });

  it("enables only the selected connection as manual canary without exposing payloads", async () => {
    const canaryPayload = {
      contract_version: "modelmirror-provider-chat-canary-v1",
      feature_enabled: true,
      policy_enabled: false,
      selected_connection_id: null,
      consent_revision: "provider-chat-canary-consent-v1",
      connections: [
        {
          connection_id: "conn-1",
          connection_name: "newAPI",
          eligible_connection: true,
          reason_code: "available",
          models: [
            {
              model_id: "provider/model",
              certification_status: "passed",
              available: false,
              reason_code: "available",
              paused: false,
              baseline_overlap: false,
              certification_expires_at: "2026-08-21T00:00:00Z",
            },
          ],
        },
      ],
      certification_max_age_seconds: 86400,
      aggregates: [
        {
          connection_id: "conn-1",
          model_id: "provider/model",
          certification_id: "cert-current",
          total_runs: 2,
          dispatched_runs: 2,
          succeeded_runs: 1,
          hard_failure_runs: 1,
          transient_failure_runs: 0,
          request_failure_runs: 0,
          cancelled_runs: 0,
          uncertain_runs: 0,
          preflight_fallback_runs: 0,
          success_rate: 0.5,
          average_ttft_ms: 125,
          average_e2e_ms: 310,
          total_tokens: 18,
          baseline_overlap: false,
          last_completed_at: "2026-08-20T00:02:00Z",
        },
      ],
      runs: [
        {
          run_id: "run-current",
          connection_id: "conn-1",
          model_id: "provider/model",
          status: "succeeded",
          dispatched: true,
          total_tokens: 18,
          baseline_overlap: false,
          current_evidence: true,
          created_at: "2026-08-20T00:01:00Z",
        },
        {
          run_id: "run-history",
          connection_id: "conn-1",
          model_id: "provider/model",
          status: "failed",
          dispatched: true,
          error_code: "timeout",
          baseline_overlap: false,
          current_evidence: false,
          stale_reason: "connection_fingerprint_changed",
          created_at: "2026-08-19T00:01:00Z",
        },
      ],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/canaries/chat") && init?.method === "PUT") {
        return response({
          ...canaryPayload,
          policy_enabled: true,
          selected_connection_id: "conn-1",
        });
      }
      if (url.includes("/canaries/chat")) return response(canaryPayload);
      return response({
        enabled: true,
        contract_version: "modelmirror-provider-chat-v1",
        certifications: [
          {
            connection_id: "conn-1",
            capability: "chat_text",
            status: "passed",
            can_run: true,
            warning_codes: [],
          },
        ],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <NewApiChatCertification
        connectionEnabled
        connectionId="conn-1"
        connectionKind="newapi"
        csrfToken="csrf-1"
      />,
    );

    const enable = await screen.findByRole("button", {
      name: "设为唯一试运行连接",
    });
    expect(screen.getByText("当前认证证据窗口")).toBeInTheDocument();
    expect(screen.getByText("成功率 50%", { exact: false })).toBeInTheDocument();
    expect(
      screen.getByText("历史证据 · connection_fingerprint_changed", {
        exact: false,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("认证有效至", { exact: false })).toBeInTheDocument();
    fireEvent.click(enable);
    await screen.findByText("已设为唯一的 newAPI Chat 试运行连接。");

    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(put).toBeDefined();
    if (!put) throw new Error("canary policy call missing");
    expect(put[1]?.headers).toMatchObject({
      "X-ModelMirror-CSRF": "csrf-1",
    });
    expect(JSON.parse(String(put[1]?.body))).toEqual({
      connection_id: "conn-1",
      enabled: true,
    });
    expect(document.body).not.toHaveTextContent("Reply with OK.");
    expect(document.body).not.toHaveTextContent("canary-secret");
  });
});
