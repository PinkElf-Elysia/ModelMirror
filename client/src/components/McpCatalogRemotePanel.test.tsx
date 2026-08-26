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
  credential_binding_ready: true,
  catalog_oauth_enabled: false,
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("McpCatalogRemotePanel", () => {
  it("shows only a server-frozen identity and keeps R4A out of Runtime", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json(staticSummary)));

    render(<McpCatalogRemotePanel projectId="catalog-remote-static" />);

    expect(await screen.findByText("https://catalog.example.com")).toBeVisible();
    expect(screen.getByText(/固定 Bearer Token · 2025-11-25/)).toBeVisible();
    expect(screen.getByText(/R4A 不激活，Runtime 工具数为 0/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /激活/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/Header 值/)).not.toBeInTheDocument();
    expect(screen.getByText(/若已保存，可直接开始复核/)).toBeVisible();
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
