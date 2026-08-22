import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ProviderChatControlSettings from "./ProviderChatControlSettings";


const policy = {
  contract_version: "modelmirror-provider-chat-routing-v1",
  feature_enabled: false,
  data_plane_integrated: false,
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

describe("ProviderChatControlSettings", () => {
  afterEach(() => vi.restoreAllMocks());

  it("keeps R5A data plane pending and saves an atomic revisioned policy", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      calls.push([input, init]);
      const url = String(input);
      if (url.endsWith("/chat-control/gate")) {
        return new Response(
          JSON.stringify({
            ready: false,
            required_activation_available: false,
            request_count: 0,
            observed_days: 0,
            success_rate: null,
            blocking_reason_codes: [
              "provider_chat_control_data_plane_pending_r5b",
            ],
          }),
        );
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
    expect(await screen.findByText("R5A 管理与资格基础")).toBeInTheDocument();
    expect(screen.getByText(/不会改变普通 Chat/)).toBeInTheDocument();
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
});
