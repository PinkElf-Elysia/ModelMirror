import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ModelServiceConnections from "./ModelServiceConnections";

describe("ModelServiceConnections editing", () => {
  afterEach(() => vi.restoreAllMocks());

  it("keeps the stored secret when blank and re-tests the edited connection", async () => {
    const connection = {
      id: "connection-1",
      name: "newAPI",
      kind: "newapi",
      base_url: "https://provider.example/v1",
      masked_key: "****cret",
      scopes: ["chat"],
      enabled: true,
      health: "online",
      model_count: 2,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/router/connections" && !init?.method) return new Response(JSON.stringify([connection]), { status: 200 });
      if (url === "/api/router/connections/connection-1" && init?.method === "PATCH") return new Response(JSON.stringify(connection), { status: 200 });
      if (url === "/api/router/connections/connection-1/test" && init?.method === "POST") return new Response(JSON.stringify({ ok: true }), { status: 200 });
      if (url.includes("/certifications")) return new Response(JSON.stringify({ enabled: true, certifications: [] }), { status: 200 });
      if (url.includes("/canaries")) return new Response(JSON.stringify({ feature_enabled: false, connections: [], runs: [], aggregates: [] }), { status: 200 });
      return new Response(null, { status: 404 });
    });

    render(<ModelServiceConnections csrfToken="csrf-test" />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "更新后的 newAPI" } });
    fireEvent.click(screen.getByLabelText("Embedding"));
    fireEvent.click(screen.getByRole("button", { name: "保存修改并测试" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/router/connections/connection-1/test" && init?.method === "POST")).toBe(true));
    const patchCall = fetchMock.mock.calls.find(([input, init]) => String(input) === "/api/router/connections/connection-1" && init?.method === "PATCH");
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({
      name: "更新后的 newAPI",
      base_url: "https://provider.example/v1",
      scopes: ["chat", "embedding"],
    });
  });

  it("offers capability-specific certification for an enabled managed OpenRouter connection", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/router/connections") {
        return new Response(
          JSON.stringify([
            {
              id: "connection-openrouter",
              name: "OpenRouter",
              kind: "openrouter",
              base_url: "https://openrouter.ai/api/v1",
              masked_key: "****test",
              scopes: ["chat", "audio"],
              enabled: true,
              health: "online",
              model_count: 419,
            },
          ]),
          { status: 200 },
        );
      }
      if (url === "/api/router/certifications/chat") {
        return new Response(
          JSON.stringify({
            enabled: true,
            contract_version: "modelmirror-provider-chat-v1",
            certifications: [
              {
                connection_id: "connection-openrouter",
                capability: "chat_text",
                status: "not_run",
                can_run: true,
                warning_codes: [],
              },
            ],
          }),
          { status: 200 },
        );
      }
      return new Response(null, { status: 404 });
    });

    render(<ModelServiceConnections csrfToken="csrf-test" />);

    expect(
      await screen.findByText("Provider Chat 能力认证"),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "运行能力认证" }),
    ).toBeDisabled();
    expect(screen.queryByText("手动会话 Canary")).toBeNull();
  });

  it("persists explicit document, image, and video scopes without changing defaults", async () => {
    const connection = {
      id: "connection-multimodal",
      name: "OpenRouter multimodal",
      kind: "openrouter",
      base_url: "https://openrouter.ai/api/v1",
      masked_key: "****test",
      scopes: ["chat"],
      enabled: true,
      health: "online",
      model_count: 2,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/router/connections" && !init?.method) return new Response(JSON.stringify([connection]), { status: 200 });
      if (url === "/api/router/connections/connection-multimodal" && init?.method === "PATCH") return new Response(JSON.stringify(connection), { status: 200 });
      if (url === "/api/router/connections/connection-multimodal/test" && init?.method === "POST") return new Response(JSON.stringify({ ok: true }), { status: 200 });
      if (url.includes("/certifications")) return new Response(JSON.stringify({ enabled: true, certifications: [] }), { status: 200 });
      return new Response(null, { status: 404 });
    });

    render(<ModelServiceConnections csrfToken="csrf-test" />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByLabelText("原生文档"));
    fireEvent.click(screen.getByLabelText("图片与 Vision"));
    fireEvent.click(screen.getByLabelText("视频能力"));
    fireEvent.click(screen.getByRole("button", { name: "保存修改并测试" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/router/connections/connection-multimodal",
      expect.objectContaining({ method: "PATCH" }),
    ));
    const patchCall = fetchMock.mock.calls.find(([input, init]) => String(input) === "/api/router/connections/connection-multimodal" && init?.method === "PATCH");
    expect(JSON.parse(String(patchCall?.[1]?.body)).scopes).toEqual([
      "chat", "document", "image", "video",
    ]);
  });
});
