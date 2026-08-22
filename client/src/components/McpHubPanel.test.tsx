import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import McpHubPanel from "./McpHubPanel";

function json(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      headers: { "Content-Type": "application/json" },
      status,
    }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("McpHubPanel", () => {
  it("shows the disabled-by-default boundary without loading candidates", async () => {
    const fetchMock = vi.fn(() =>
      json({
        enabled: false,
        remote_enabled: false,
        source: "https://registry.modelcontextprotocol.io",
        snapshot_at: 0,
        snapshot_count: 0,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    expect(await screen.findByText(/功能开关默认关闭/)).toBeVisible();
    expect(screen.getByText(/Registry 收录不代表安全认证/)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("submits only Registry identifiers when adding a candidate", async () => {
    let candidateAdded = false;
    const candidate = {
      candidate_id: "mcphub_" + "2".repeat(32),
      server_name: "io.example/public",
      version: "1.2.3",
      state: "draft",
      origin: "https://mcp.example.com",
      schema_digest: "",
      tools: [],
      connected: false,
      taint_reason: "",
      activation_eligible: false,
      activation_reason: "hub_preflight_required",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({
          enabled: true,
          remote_enabled: true,
          source: "https://registry.modelcontextprotocol.io",
          snapshot_at: 1,
          snapshot_count: 1,
        });
      }
      if (url.includes("/api/mcp/hub/servers?")) {
        return json({
          items: [
            {
              server_name: "io.example/public",
              version: "1.2.3",
              title: "Public Example",
              description: "Public metadata",
              status: "active",
              eligibility: "eligible",
              remotes: [
                {
                  remote_id: "remote_1111111111111111",
                  transport: "streamable-http",
                  origin: "https://mcp.example.com",
                  eligibility: "eligible",
                  reason: "可进行匿名只读远程试连",
                },
              ],
            },
          ],
          total: 1,
          next_cursor: null,
          categories: ["search"],
        });
      }
      if (url.endsWith("/api/mcp/hub/candidates") && init?.method === "POST") {
        candidateAdded = true;
        return json(candidate, 201);
      }
      if (url.endsWith("/api/mcp/hub/candidates")) return json({ items: candidateAdded ? [candidate] : [] });
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "添加到我的 MCP" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/api/mcp/hub/candidates") &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(String((call?.[1] as RequestInit).body));
      expect(body).toEqual({
        server_name: "io.example/public",
        version: "1.2.3",
        remote_id: "remote_1111111111111111",
      });
      expect(JSON.stringify(body)).not.toContain("https://");
      expect(JSON.stringify(body)).not.toContain("command");
      expect(JSON.stringify(body)).not.toContain("headers");
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Public Example 已添加到“我的 Hub 连接”");
    expect(screen.getByRole("button", { name: "已添加" })).toBeDisabled();
    await waitFor(() => {
      expect(screen.getByRole("region", { name: "我的 Hub 连接" })).toHaveFocus();
    });
  });

  it("uses the preflight digest verbatim for activation", async () => {
    const candidate = {
      candidate_id: "mcphub_" + "3".repeat(32),
      server_name: "io.example/public",
      version: "1.2.3",
      state: "verified",
      origin: "https://mcp.example.com",
      schema_digest: "a".repeat(64),
      tools: [{ name: "search", description: "", schema_digest: "b".repeat(64) }],
      connected: true,
      taint_reason: "",
      activation_eligible: true,
      activation_reason: "",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({ enabled: true, remote_enabled: true, source: "registry", snapshot_at: 1, snapshot_count: 0 });
      }
      if (url.includes("/api/mcp/hub/servers?")) return json({ items: [], total: 0, next_cursor: null, categories: [] });
      if (url.endsWith("/api/mcp/hub/candidates") && !init?.method) return json({ items: [candidate] });
      if (url.endsWith(`/api/mcp/hub/candidates/${candidate.candidate_id}/activate`)) return json({ ...candidate, state: "active" });
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "激活" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/activate"));
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({
        expected_schema_digest: "a".repeat(64),
      });
    });
  });

  it("keeps an unreviewed verified candidate visible but not activatable", async () => {
    const candidate = {
      candidate_id: "mcphub_" + "9".repeat(32),
      server_name: "io.example/unreviewed",
      version: "1.0.0",
      state: "verified",
      origin: "https://unreviewed.example.com",
      schema_digest: "c".repeat(64),
      tools: [{ name: "publish", description: "", schema_digest: "d".repeat(64) }],
      connected: true,
      taint_reason: "",
      activation_eligible: false,
      activation_reason: "hub_contract_unreviewed",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({ enabled: true, remote_enabled: true, source: "registry", snapshot_at: 1, snapshot_count: 1 });
      }
      if (url.includes("/api/mcp/hub/servers?")) return json({ items: [], total: 0, next_cursor: null, categories: [] });
      if (url.endsWith("/api/mcp/hub/candidates")) return json({ items: [candidate] });
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    expect(await screen.findByText(/尚未完成 ModelMirror 执行契约复核/)).toBeVisible();
    expect(screen.getByRole("button", { name: "激活" })).toBeDisabled();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/activate"))).toBe(false);
  });

  it("applies search filters and paginates the fixed Registry snapshot", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({ enabled: true, remote_enabled: true, source: "registry", snapshot_at: 1, snapshot_count: 80 });
      }
      if (url.endsWith("/api/mcp/hub/candidates")) return json({ items: [] });
      if (url.includes("/api/mcp/hub/servers?")) {
        const parsed = new URL(url, "http://localhost");
        const cursor = parsed.searchParams.get("cursor");
        return json({
          items: [{
            server_name: cursor === "50" ? "io.example/page-two" : "io.example/page-one",
            version: "1.0.0",
            title: cursor === "50" ? "Page Two" : "Page One",
            description: "Public metadata",
            status: "active",
            eligibility: "no_remote",
            remotes: [],
          }],
          total: 80,
          next_cursor: cursor === "50" ? null : 50,
          categories: ["research"],
        });
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    expect(await screen.findByText("Page One")).toBeVisible();
    fireEvent.change(screen.getByPlaceholderText("搜索名称或用途"), { target: { value: "docs" } });
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("q=docs"))).toBe(true);
    });
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByText("Page Two")).toBeVisible();
    expect(screen.getByText(/第 2 页/)).toBeVisible();
  });

  it("keeps a candidate visible and reports a failed preflight", async () => {
    const candidate = {
      candidate_id: "mcphub_" + "4".repeat(32),
      server_name: "io.example/public",
      version: "1.2.3",
      state: "draft",
      origin: "https://mcp.example.com",
      schema_digest: "",
      tools: [],
      connected: false,
      taint_reason: "",
      activation_eligible: false,
      activation_reason: "hub_preflight_required",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({ enabled: true, remote_enabled: true, source: "registry", snapshot_at: 1, snapshot_count: 0 });
      }
      if (url.includes("/api/mcp/hub/servers?")) return json({ items: [], total: 0, next_cursor: null, categories: [] });
      if (url.endsWith("/api/mcp/hub/candidates") && !init?.method) return json({ items: [candidate] });
      if (url.endsWith(`/api/mcp/hub/candidates/${candidate.candidate_id}/preflight`)) {
        return json({ detail: { error: "远程 Schema 校验失败" } }, 409);
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "安全预检" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("远程 Schema 校验失败");
    expect(screen.getByText("io.example/public")).toBeVisible();
    expect(screen.getByRole("button", { name: "安全预检" })).toBeEnabled();
  });

  it("creates a local-operator review batch with Registry identifiers only", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({ enabled: true, remote_enabled: true, source: "registry", snapshot_at: 1, snapshot_count: 1 });
      }
      if (url.endsWith("/api/mcp/hub/reviews/status")) {
        return json({
          enabled: true,
          local_publish_enabled: false,
          signing_key_configured: false,
          sop_version: "anonymous_https_tools_v1",
          max_batch_size: 20,
          max_concurrency: 2,
          active_run_id: null,
          operator_scope: "trusted-local-operator",
          multi_tenant_admin: false,
        });
      }
      if (url.includes("/api/mcp/hub/servers?")) {
        return json({
          items: [{
            server_name: "io.example/review",
            version: "1.0.0",
            title: "Review Example",
            description: "Public metadata",
            status: "active",
            eligibility: "eligible",
            remotes: [{
              remote_id: "remote_1111111111111111",
              transport: "streamable-http",
              origin: "https://review.example.com",
              eligibility: "eligible",
              reason: "eligible",
            }],
          }],
          total: 1,
          next_cursor: null,
          categories: [],
        });
      }
      if (url.endsWith("/api/mcp/hub/candidates")) return json({ items: [] });
      if (url.endsWith("/api/mcp/hub/review-runs") && init?.method === "POST") {
        return json({ run_id: "hubreview_" + "1".repeat(32), status: "queued", items: [], counts: {} }, 201);
      }
      if (url.endsWith("/api/mcp/hub/review-runs")) return json({ items: [] });
      if (url.endsWith("/api/mcp/hub/contracts")) return json({ items: [] });
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    expect(await screen.findByText("复核工作台")).toBeVisible();
    fireEvent.click(screen.getByRole("checkbox", { name: "加入受控复核批次" }));
    fireEvent.click(screen.getByRole("button", { name: "创建受控复核批次" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url, init]) => (
        String(url).endsWith("/api/mcp/hub/review-runs") &&
        (init as RequestInit | undefined)?.method === "POST"
      ));
      expect(call).toBeTruthy();
      const body = JSON.parse(String((call?.[1] as RequestInit).body));
      expect(body).toEqual({
        items: [{
          server_name: "io.example/review",
          version: "1.0.0",
          remote_id: "remote_1111111111111111",
        }],
      });
      expect(JSON.stringify(body)).not.toContain("https://");
      expect(JSON.stringify(body)).not.toContain("arguments");
    });
    expect(screen.getByText(/本地运维者功能/)).toBeVisible();
    expect(screen.getByText(/不是多租户管理员权限/)).toBeVisible();
  });
});
