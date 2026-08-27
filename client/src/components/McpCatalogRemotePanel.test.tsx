import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import McpCatalogRemotePanel from "./McpCatalogRemotePanel";

function json(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      headers: { "Content-Type": "application/json" },
      status,
    }),
  );
}

const staticSummary = {
  project_id: "catalog-remote-static",
  origin: "https://catalog.example.com",
  version: "1.2.3",
  protocol_version: "2025-11-25",
  auth_mode: "static_bearer",
  target_state: { state: "draft", reason_code: "" },
  oauth: null,
  reviewed_contract: null,
  contract_error: "mcp_remote_contract_unreviewed",
  activation_eligible: false,
  runtime_tool_count: 0,
  runtime_enabled: false,
  catalog_runtime_enabled: false,
  credential_binding_ready: true,
  catalog_oauth_enabled: false,
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("McpCatalogRemotePanel", () => {
  it("shows only a server-frozen identity and keeps unreviewed targets out of Runtime", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json(staticSummary)));

    render(<McpCatalogRemotePanel projectId="catalog-remote-static" />);

    expect(await screen.findByText("https://catalog.example.com")).toBeVisible();
    expect(screen.getByText(/固定 Bearer Token · 2025-11-25/)).toBeVisible();
    expect(screen.getByText(/未激活；未复核目标不会进入 Runtime/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /激活/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/Header 值/)).not.toBeInTheDocument();
    expect(screen.getByText(/若已保存，可直接开始复核/)).toBeVisible();
  });

  it("activates and disconnects only with the reviewed contract fingerprint", async () => {
    let active = false;
    const reviewed = {
      ...staticSummary,
      runtime_enabled: true,
      catalog_runtime_enabled: true,
      activation_eligible: true,
      reviewed_contract: {
        contract_id: "catalogct_" + "a".repeat(32),
        contract_fingerprint: "b".repeat(64),
      },
      target_state: { state: "reviewed", reason_code: "" },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/remote/review-runs") && !init?.method) {
        return json({ items: [], total: 0 });
      }
      if (url.endsWith("/api/mcp/catalog/catalog-remote-static/remote/activate")) {
        active = true;
        return json({
          ...reviewed,
          target_state: { state: "active", reason_code: "" },
          runtime_tool_count: 1,
        });
      }
      if (url.endsWith("/api/mcp/catalog/catalog-remote-static/remote/session")) {
        active = false;
        return json(reviewed);
      }
      if (url.endsWith("/api/mcp/catalog/catalog-remote-static/remote")) {
        return json(active
          ? {
            ...reviewed,
            target_state: { state: "active", reason_code: "" },
            runtime_tool_count: 1,
          }
          : reviewed);
      }
      throw new Error(`unexpected URL: ${url} ${init?.method || "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpCatalogRemotePanel projectId="catalog-remote-static" />);

    fireEvent.click(await screen.findByRole("button", { name: "激活 Runtime" }));

    await waitFor(() => expect(screen.getByText(/已激活 · Runtime 工具 1 个/)).toBeVisible());
    const activate = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/remote/activate"),
    );
    expect(JSON.parse(String((activate?.[1] as RequestInit).body))).toEqual({
      expected_contract_fingerprint: "b".repeat(64),
    });
    fireEvent.click(screen.getByRole("button", { name: "断开 Runtime" }));
    await waitFor(() => expect(screen.getByText(/契约已复核，可显式激活/)).toBeVisible());
    const disconnect = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/remote/session"),
    );
    expect((disconnect?.[1] as RequestInit).method).toBe("DELETE");
    expect((disconnect?.[1] as RequestInit).body).toBeUndefined();
  });

  it("does not invite secret entry until the external-key broker gate is ready", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => json({ ...staticSummary, credential_binding_ready: false })),
    );

    render(<McpCatalogRemotePanel projectId="catalog-remote-static" />);

    expect(await screen.findByText(/固定凭据槽尚未开放/)).toBeVisible();
    expect(screen.getByText(/未满足前不会保存 Secret/)).toBeVisible();
    expect(screen.queryByText(/若已保存，可直接开始复核/)).not.toBeInTheDocument();
  });

  it("explains a tools-only capability rejection without blaming credentials", async () => {
    const run = {
      run_id: "remreview_" + "1".repeat(32),
      status: "completed",
      error_code: "",
      items: [
        {
          item_id: "remitem_" + "2".repeat(32),
          state: "blocked",
          error_code: "hub_non_tool_capability_denied",
          evidence_digest: "",
          contract_fingerprint: "",
          proposal: null,
          evidence: null,
        },
      ],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/remote/review-runs") && !init?.method) {
        return json({ items: [], total: 0 });
      }
      if (url.endsWith("/api/mcp/catalog/catalog-remote-static/remote")) {
        return json(staticSummary);
      }
      if (url.endsWith("/api/mcp/remote/review-runs") && init?.method === "POST") {
        return json(run, 201);
      }
      if (url.endsWith(`/api/mcp/remote/review-runs/${run.run_id}`)) {
        return json(run);
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpCatalogRemotePanel projectId="catalog-remote-static" />);

    fireEvent.click(await screen.findByRole("button", { name: "开始复核" }));

    expect(await screen.findByText("hub_non_tool_capability_denied")).toBeVisible();
    expect(screen.getByText(/额外能力，已安全阻断；这不是凭据错误/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重新复核" }));
    expect(screen.getByRole("button", { name: "开始复核" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(/可以重新开始复核/);
  });

  it("blocks OAuth registration and review when frozen scopes are high risk", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => json({
        ...staticSummary,
        auth_mode: "oauth_authorization_code_pkce",
        catalog_oauth_enabled: true,
        oauth: {
          discovery: {
            discovery_fingerprint: "a".repeat(64),
            issuer: "https://auth.example.com",
            scope_source: "protected_resource_metadata",
            recommended_scopes: ["event:write", "org:read", "project:write"],
            recommended_scope_digest: "b".repeat(64),
            offline_access_available: false,
          },
          registration: null,
          authorization_session: null,
          token: null,
          scope_assessment: {
            classification: "dangerous",
            dangerous_scopes: ["event:write", "project:write"],
            unknown_scopes: [],
            read_candidate_scopes: ["org:read"],
          },
        },
      })),
    );

    render(<McpCatalogRemotePanel projectId="catalog-oauth" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/高危写入或控制语义/);
    expect(screen.getByRole("button", { name: "登记 client" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "开始复核" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "创建授权链接" })).not.toBeInTheDocument();
  });

  it("creates a review with only the Catalog target identity", async () => {
    const run = {
      run_id: "remreview_" + "1".repeat(32),
      status: "running",
      error_code: "",
      items: [
        {
          item_id: "remitem_" + "2".repeat(32),
          state: "running",
          error_code: "",
          evidence_digest: "",
          contract_fingerprint: "",
          proposal: null,
          evidence: null,
        },
      ],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/remote/review-runs") && !init?.method) {
        return json({ items: [], total: 0 });
      }
      if (url.endsWith("/api/mcp/catalog/catalog-remote-static/remote")) {
        return json(staticSummary);
      }
      if (url.endsWith("/api/mcp/remote/review-runs") && init?.method === "POST") {
        return json(run, 201);
      }
      if (url.endsWith(`/api/mcp/remote/review-runs/${run.run_id}`)) {
        return json({ ...run, status: "awaiting_operator" });
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpCatalogRemotePanel projectId="catalog-remote-static" />);

    fireEvent.click(await screen.findByRole("button", { name: "开始复核" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/api/mcp/remote/review-runs") &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      const body = JSON.parse(String((call?.[1] as RequestInit).body));
      expect(body).toEqual({
        items: [
          { target_type: "catalog_project", target_id: "catalog-remote-static" },
        ],
      });
      expect(JSON.stringify(body)).not.toContain("https://");
      expect(JSON.stringify(body)).not.toContain("header");
      expect(JSON.stringify(body)).not.toContain("scope");
      expect(JSON.stringify(body)).not.toContain("tenant");
      expect(JSON.stringify(body)).not.toContain("owner");
    });
  });

  it("shows the complete server-generated proposal before approving one call", async () => {
    const proposalDigest = "d".repeat(64);
    const run = {
      run_id: "remreview_" + "1".repeat(32),
      status: "awaiting_operator",
      error_code: "",
      items: [
        {
          item_id: "remitem_" + "2".repeat(32),
          state: "awaiting_call_approval",
          error_code: "",
          evidence_digest: "e".repeat(64),
          contract_fingerprint: "",
          proposal: {
            proposal_id: "remproposal_" + "3".repeat(32),
            proposal_digest: proposalDigest,
            tool_name: "search_code",
            arguments: { query: "modelmirror-probe", perPage: 1 },
          },
          evidence: {
            effect_proposals: { search_code: "read_candidate" },
            schema_digest: "f".repeat(64),
            cleanup: { session_closed: true },
          },
        },
      ],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/remote/review-runs") && !init?.method) {
        return json({ items: [], total: 0 });
      }
      if (url.endsWith("/api/mcp/catalog/catalog-remote-static/remote")) {
        return json(staticSummary);
      }
      if (url.endsWith("/api/mcp/remote/review-runs") && init?.method === "POST") {
        return json(run, 201);
      }
      if (url.endsWith(`/api/mcp/remote/review-runs/${run.run_id}`)) {
        return json(run);
      }
      if (url.includes("/call-proposals/") && url.endsWith("/approve")) {
        return json(run);
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpCatalogRemotePanel projectId="catalog-remote-static" />);

    fireEvent.click(await screen.findByRole("button", { name: "开始复核" }));

    expect(await screen.findByText(/modelmirror-probe/)).toBeVisible();
    expect(screen.getByText(`Proposal ${proposalDigest}`)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "批准一次代表读取" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/call-proposals/"));
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({
        expected_proposal_digest: proposalDigest,
      });
    });
  });

  it("restores the latest persisted review for this Catalog target", async () => {
    const run = {
      run_id: "remreview_" + "4".repeat(32),
      status: "awaiting_operator",
      error_code: "",
      items: [
        {
          item_id: "remitem_" + "5".repeat(32),
          state: "awaiting_call_approval",
          error_code: "",
          evidence_digest: "e".repeat(64),
          contract_fingerprint: "",
          proposal: {
            proposal_id: "remproposal_" + "6".repeat(32),
            proposal_digest: "d".repeat(64),
            tool_name: "search_code",
            arguments: { query: "persisted-probe" },
          },
          evidence: {
            effect_proposals: { search_code: "read_candidate" },
            schema_digest: "f".repeat(64),
            cleanup: { session_closed: true },
          },
          target: {
            target_type: "catalog_project",
            target_id: "catalog-remote-static",
          },
        },
      ],
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/catalog/catalog-remote-static/remote")) {
        return json(staticSummary);
      }
      if (url.endsWith("/api/mcp/remote/review-runs")) {
        return json({ items: [run], total: 1 });
      }
      throw new Error(`unexpected URL: ${url}`);
    }));

    render(<McpCatalogRemotePanel projectId="catalog-remote-static" />);

    expect(await screen.findByText(/persisted-probe/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "开始复核" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准一次代表读取" })).toBeVisible();
  });

  it("allows a published target to be reviewed again after credential drift", async () => {
    const run = {
      run_id: "remreview_" + "9".repeat(32),
      status: "completed",
      error_code: "",
      items: [
        {
          item_id: "remitem_" + "a".repeat(32),
          state: "published",
          error_code: "",
          evidence_digest: "e".repeat(64),
          contract_fingerprint: "b".repeat(64),
          proposal: null,
          evidence: { schema_digest: "f".repeat(64) },
          target: {
            target_type: "catalog_project",
            target_id: "catalog-remote-static",
          },
        },
      ],
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/catalog/catalog-remote-static/remote")) {
        return json({
          ...staticSummary,
          target_state: {
            state: "drifted",
            reason_code: "mcp_remote_auth_binding_revision_changed",
          },
          reviewed_contract: {
            contract_id: "catalogct_" + "a".repeat(32),
            contract_fingerprint: "b".repeat(64),
          },
        });
      }
      if (url.endsWith("/api/mcp/remote/review-runs")) {
        return json({ items: [run], total: 1 });
      }
      throw new Error(`unexpected URL: ${url}`);
    }));

    render(<McpCatalogRemotePanel projectId="catalog-remote-static" />);

    expect(await screen.findByText("已漂移")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重新复核" }));
    expect(screen.getByRole("button", { name: "开始复核" })).toBeVisible();
  });

  it("requires an explicit least-privilege tool subset decision", async () => {
    const run = {
      run_id: "remreview_" + "7".repeat(32),
      status: "awaiting_operator",
      error_code: "",
      items: [
        {
          item_id: "remitem_" + "8".repeat(32),
          state: "awaiting_decision",
          error_code: "",
          evidence_digest: "e".repeat(64),
          contract_fingerprint: "",
          proposal: null,
          evidence: {
            effect_proposals: {
              list_releases: "read_candidate",
              search_code: "read_candidate",
            },
            schema_digest: "f".repeat(64),
            cleanup: { session_closed: true },
          },
          target: {
            target_type: "catalog_project",
            target_id: "catalog-remote-static",
          },
        },
      ],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/catalog/catalog-remote-static/remote")) {
        return json(staticSummary);
      }
      if (url.endsWith("/api/mcp/remote/review-runs") && !init?.method) {
        return json({ items: [run], total: 1 });
      }
      if (url.endsWith(`/api/mcp/remote/review-runs/${run.run_id}`)) {
        return json(run);
      }
      if (url.endsWith("/decision") && init?.method === "POST") {
        return json(run);
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<McpCatalogRemotePanel projectId="catalog-remote-static" />);

    const approve = await screen.findByRole("button", { name: "批准只读契约草案" });
    expect(screen.getByRole("checkbox", { name: "list_releases" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "search_code" })).not.toBeChecked();
    expect(approve).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "search_code" }));
    expect(approve).toBeEnabled();
    fireEvent.click(approve);

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/decision"));
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({
        decision: "approve",
        expected_evidence_digest: "e".repeat(64),
        allowed_tools: ["search_code"],
        tool_effects: { search_code: "read" },
      });
    });
  });

  it("submits only server-issued OAuth digests and the refresh-token decision", async () => {
    let tokenActive = false;
    const oauthSummary = {
      ...staticSummary,
      auth_mode: "oauth_authorization_code_pkce",
      catalog_oauth_enabled: true,
      oauth: {
        discovery: {
          discovery_fingerprint: "a".repeat(64),
          issuer: "https://auth.example.com",
          scope_source: "protected_resource_metadata",
          recommended_scopes: ["mcp:read"],
          recommended_scope_digest: "b".repeat(64),
          offline_access_available: true,
        },
        registration: {
          registration_digest: "c".repeat(64),
          mode: "pre_registered",
          status: "active",
        },
        authorization_session: null,
        token: null,
      },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/remote/review-runs") && !init?.method) {
        return json({ items: [], total: 0 });
      }
      if (url.endsWith("/api/mcp/catalog/catalog-oauth/remote")) {
        return json(tokenActive
          ? {
            ...oauthSummary,
            oauth: {
              ...oauthSummary.oauth,
              token: {
                token_id: "token-local",
                revision: 1,
                status: "active",
                scopes: ["mcp:read"],
                refresh_available: false,
                resource_bound: true,
              },
            },
          }
          : oauthSummary);
      }
      if (url.endsWith("/api/mcp/catalog/catalog-oauth/remote/oauth/authorize")) {
        return json({ authorization_url: "https://auth.example.com/authorize?state=server-owned" });
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpCatalogRemotePanel projectId="catalog-oauth" />);

    const checkbox = await screen.findByRole("checkbox");
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: "创建授权链接" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) =>
        String(url).endsWith("/api/mcp/catalog/catalog-oauth/remote/oauth/authorize"),
      );
      const body = JSON.parse(String((call?.[1] as RequestInit).body));
      expect(body).toEqual({
        expected_discovery_fingerprint: "a".repeat(64),
        expected_registration_digest: "c".repeat(64),
        expected_scope_digest: "b".repeat(64),
        request_refresh_token: true,
      });
      expect(JSON.stringify(body)).not.toContain("https://");
      expect(JSON.stringify(body)).not.toContain("client_id");
      expect(JSON.stringify(body)).not.toContain("header");
      expect(JSON.stringify(body)).not.toContain("mcp:read");
    });
    expect(await screen.findByRole("link", { name: /打开授权页面/ })).toHaveAttribute(
      "href",
      "https://auth.example.com/authorize?state=server-owned",
    );

    tokenActive = true;
    fireEvent.click(screen.getByRole("button", { name: "刷新远程复核状态" }));

    await waitFor(() => {
      expect(screen.queryByRole("link", { name: /打开授权页面/ })).not.toBeInTheDocument();
    });
    expect(screen.getByText(/Token 已加密保存 · revision 1/)).toBeInTheDocument();
  });
});
