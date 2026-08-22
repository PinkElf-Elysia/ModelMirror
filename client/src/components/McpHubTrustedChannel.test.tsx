import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import McpHubTrustedChannel from "./McpHubTrustedChannel";

function json(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      headers: { "Content-Type": "application/json" },
      status,
    }),
  );
}

const fingerprint = "a".repeat(64);
const contractId = "hubct_" + "b".repeat(32);

function trustedItem(state: "ready" | "stale" | "environment_blocked" = "stale") {
  return {
    contract_id: contractId,
    contract_fingerprint: fingerprint,
    contract_source: "repository",
    server_name: "io.example/trusted",
    version: "1.0.0",
    title: "Trusted Search",
    description: "Search public metadata",
    publisher: "Example Publisher",
    categories: ["search"],
    origin: "https://trusted.example.com",
    allowed_tools: ["search"],
    availability_state: state,
    health_checked_at: 0,
    health_error_code: state === "environment_blocked" ? "hub_dns_private_or_synthetic_denied" : "",
    candidate_id: "",
    candidate_state: "",
    connected: false,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("McpHubTrustedChannel", () => {
  it("activates by immutable contract identity without sending a URL", async () => {
    let active = false;
    const changed = vi.fn();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/trusted/status")) {
        return json({ enabled: true, auto_review_enabled: true, health_ttl_seconds: 86400, total: 1, counts: { stale: 1 } });
      }
      if (url.includes("/api/mcp/hub/trusted/servers?") && !init?.method) {
        return json({ items: [{ ...trustedItem(active ? "ready" : "stale"), candidate_state: active ? "active" : "" }], total: 1, next_cursor: null });
      }
      if (url.endsWith(`/api/mcp/hub/trusted/servers/${contractId}/activate`)) {
        active = true;
        return json({ candidate_id: "mcphub_" + "1".repeat(32), state: "active" });
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubTrustedChannel onChanged={changed} />);

    expect(await screen.findByRole("option", { name: "已撤销" })).toBeVisible();
    expect(screen.getByRole("option", { name: "契约冲突" })).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: "复核并连接" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    const activationCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/activate"));
    expect(JSON.parse(String((activationCall?.[1] as RequestInit).body))).toEqual({
      expected_contract_fingerprint: fingerprint,
    });
    expect(JSON.stringify((activationCall?.[1] as RequestInit).body)).not.toContain("https://");
    expect(await screen.findByText("已加入我的 MCP")).toBeVisible();
  });

  it("explains a local isolation denial without calling it contract drift", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/trusted/status")) {
        return json({ enabled: true, auto_review_enabled: false, health_ttl_seconds: 86400, total: 1, counts: { environment_blocked: 1 } });
      }
      if (url.includes("/api/mcp/hub/trusted/servers?")) {
        return json({ items: [trustedItem("environment_blocked")], total: 1, next_cursor: null });
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubTrustedChannel />);

    expect((await screen.findAllByText("当前环境已阻断")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/这不代表远程服务存在安全问题/)).toBeVisible();
    expect(screen.getAllByText("契约已漂移")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "重新检查" })).toBeEnabled();
  });

  it("keeps a failed recheck visible after refreshing channel state", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/trusted/status")) {
        return json({ enabled: true, auto_review_enabled: false, health_ttl_seconds: 86400, total: 1, counts: { environment_blocked: 1 } });
      }
      if (url.includes("/api/mcp/hub/trusted/servers?") && !init?.method) {
        return json({ items: [trustedItem("environment_blocked")], total: 1, next_cursor: null });
      }
      if (url.endsWith(`/api/mcp/hub/trusted/servers/${contractId}/revalidate`)) {
        return json({ detail: { code: "hub_trusted_recheck_rate_limited", error: "该契约刚刚完成检查，请稍后再试。" } }, 429);
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubTrustedChannel />);

    fireEvent.click(await screen.findByRole("button", { name: "重新检查" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("该契约刚刚完成检查，请稍后再试。");
  });

  it("reloads a preserved search result when a sibling revokes its contract", async () => {
    let revoked = false;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/trusted/status")) {
        return json({
          enabled: true,
          auto_review_enabled: false,
          health_ttl_seconds: 86400,
          total: 1,
          counts: revoked ? { revoked: 1 } : { ready: 1 },
        });
      }
      if (url.includes("/api/mcp/hub/trusted/servers?")) {
        return json({
          items: [{ ...trustedItem(revoked ? "stale" : "ready"), availability_state: revoked ? "revoked" : "ready" }],
          total: 1,
          next_cursor: null,
        });
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(<McpHubTrustedChannel refreshToken={0} />);

    fireEvent.change(await screen.findByRole("searchbox", { name: "搜索可信 MCP" }), {
      target: { value: "Trusted" },
    });
    expect(await screen.findByRole("button", { name: "连接服务" })).toBeVisible();

    revoked = true;
    rerender(<McpHubTrustedChannel refreshToken={1} />);

    expect(await screen.findByText("已撤销")).toBeVisible();
    expect(screen.getByRole("searchbox", { name: "搜索可信 MCP" })).toHaveValue("Trusted");
    expect(screen.getByText("当前不可连接")).toBeVisible();
  });
});
