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
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() =>
        response({
          enabled: true,
          contract_version: "modelmirror-provider-chat-v1",
          certifications: [
            {
              connection_id: "conn-1",
              status: "not_run",
              can_run: true,
              warning_codes: [],
            },
          ],
        }),
      )
      .mockImplementationOnce(() =>
        response({
          ok: true,
          model_ids: ["provider/model"],
          model_count: 1,
          checked_at: "2026-08-20T00:00:00Z",
          truncated: false,
          message: "ok",
        }),
      )
      .mockImplementationOnce(() =>
        response({
          enabled: true,
          contract_version: "modelmirror-provider-chat-v1",
          certifications: [
            {
              connection_id: "conn-1",
              status: "not_run",
              can_run: true,
              warning_codes: [],
            },
          ],
        }),
      )
      .mockImplementationOnce(() =>
        response({
          certification_id: "cert-1",
          connection_id: "conn-1",
          status: "passed",
          can_run: true,
          warning_codes: [],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "idempotency-1" });

    render(
      <NewApiChatCertification
        connectionEnabled
        connectionId="conn-1"
        csrfToken="csrf-1"
      />,
    );

    expect(screen.getByText("不代表默认数据面已就绪", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "运行 Chat 认证" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "刷新可认证模型" }));
    await screen.findByRole("option", { name: "provider/model" });
    fireEvent.click(screen.getByRole("button", { name: "运行 Chat 认证" }));

    expect(screen.getByRole("dialog")).toHaveTextContent("最多一次真实 Chat 请求");
    expect(screen.getByRole("dialog")).toHaveTextContent("可能产生少量额度费用");
    expect(fetchMock).toHaveBeenCalledTimes(3);

    fireEvent.click(screen.getByRole("button", { name: "确认并运行一次" }));
    await waitFor(() =>
      expect(screen.getAllByText("核心文本 Chat 已通过")).toHaveLength(2),
    );

    const certificationCall = fetchMock.mock.calls[3];
    expect(certificationCall[0]).toContain("/certifications/chat");
    expect(certificationCall[1]).toMatchObject({
      method: "POST",
      headers: expect.objectContaining({
        "Idempotency-Key": "idempotency-1",
        "X-ModelMirror-CSRF": "csrf-1",
      }),
    });
    expect(JSON.parse(certificationCall[1].body)).toEqual({
      model_id: "provider/model",
      acknowledge_billed_call: true,
    });
  });

  it("shows disabled state without offering a certification call", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        response({
          enabled: false,
          contract_version: "modelmirror-provider-chat-v1",
          certifications: [
            {
              connection_id: "conn-1",
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
        csrfToken="csrf-1"
      />,
    );

    await waitFor(() =>
      expect(screen.getByText("部署已关闭 Chat 认证操作。")).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "运行 Chat 认证" })).toBeDisabled();
  });
});
