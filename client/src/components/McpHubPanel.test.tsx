import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mockOAuthAuthorization() {
  const candidateId = "mcphub_" + "a".repeat(32);
  const session = { session_id: "", status: "pending", expires_at: 0, scopes: ["mcp:read"] };
  const candidate = {
    candidate_id: candidateId, server_name: "io.example/expiry", version: "1.0.0",
    state: "draft", origin: "https://mcp.example.com", source_digest: "b".repeat(64),
    schema_digest: "", tools: [], connected: false, activation_eligible: false,
    oauth_discovery_available: true,
  };
  const oauth = () => ({
    discovery: {
      discovery_id: "discovery", discovery_fingerprint: "c".repeat(64), status: "active",
      resource_uri: "https://mcp.example.com/mcp", issuer: "https://auth.example.com",
      pkce_method: "S256", token_endpoint_origin: "https://auth.example.com",
      recommended_scopes: ["mcp:read"], recommended_scope_digest: "d".repeat(64),
      offline_access_available: false,
    },
    registration: {
      registration_id: "registration", registration_digest: "e".repeat(64),
      status: "active", mode: "pre_registered", client_id: "public-client", revision: 1,
    },
    authorization_session: session.session_id ? { ...session } : null,
    token: null,
  });
  let created = 0;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/mcp/hub/status")) {
      return json({ enabled: true, remote_enabled: true, snapshot_count: 0, snapshot_at: 1 });
    }
    if (url.endsWith("/api/mcp/remote-auth/oauth/status")) {
      return json({
        enabled: true, remote_auth_enabled: true, single_owner_acknowledged: true,
        external_master_key_available: true, external_master_key_enforced: true, storage_ready: true,
        authorization_enabled: true, token_storage_enabled: true, supported_registration_modes: ["pre_registered"],
      });
    }
    if (url.includes("/api/mcp/hub/servers?")) return json({ items: [], total: 0, next_cursor: null });
    if (url.endsWith("/api/mcp/hub/candidates")) return json({ items: [candidate] });
    if (url.endsWith(`/candidates/${candidateId}/oauth`) && !init?.method) return json(oauth());
    if (url.endsWith("/oauth/authorization-sessions") && init?.method === "POST") {
      created += 1;
      Object.assign(session, { session_id: `session-${created}`, status: "pending", expires_at: Date.now() / 1000 + 600 });
      return json({ authorization_session: { ...session }, authorization_url: `https://auth.example.com/authorize?state=fresh-${created}` });
    }
    return json({ enabled: false });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { session, fetchMock };
}

describe("McpHubPanel", () => {
  it.each(["server status", "elapsed deadline"])("replaces an expired OAuth link using %s without replaying the old session", async (expirySource) => {
    const { session, fetchMock } = mockOAuthAuthorization();
    render(<McpHubPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "创建授权链接" }));
    expect(await screen.findByRole("link", { name: "打开授权页面" })).toHaveAttribute("href", "https://auth.example.com/authorize?state=fresh-1");
    if (expirySource === "server status") session.status = "expired";
    else session.expires_at = Date.now() / 1000 - 1;
    fireEvent.click(screen.getByRole("button", { name: "刷新授权状态" }));
    expect(await screen.findByText("授权链接已过期")).toBeVisible();
    expect(screen.queryByRole("link", { name: "打开授权页面" })).not.toBeInTheDocument();
    expect(screen.getByText(/旧授权码不会重试/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "创建新授权链接" }));
    expect(await screen.findByRole("link", { name: "打开授权页面" })).toHaveAttribute("href", "https://auth.example.com/authorize?state=fresh-2");
    const writes = fetchMock.mock.calls.filter(([, init]) => init?.method);
    expect(writes).toHaveLength(2);
    for (const [url, init] of writes) {
      expect(String(url)).toMatch(/\/oauth\/authorization-sessions$/);
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({
        expected_discovery_fingerprint: "c".repeat(64), expected_registration_digest: "e".repeat(64),
        expected_scope_digest: "d".repeat(64), request_refresh_token: false,
      });
    }
  });

  it("expires an idle OAuth link without making a background request", async () => {
    const { fetchMock } = mockOAuthAuthorization();
    render(<McpHubPanel />);
    const create = await screen.findByRole("button", { name: "创建授权链接" });
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(create); });
    expect(screen.getByRole("link", { name: "打开授权页面" })).toBeVisible();
    const calls = fetchMock.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(600_001); });
    expect(screen.getByText("授权链接已过期")).toBeVisible();
    expect(screen.queryByRole("link", { name: "打开授权页面" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(calls);
  });

  it("blocks a late click before the browser expiry timer runs", async () => {
    const { session, fetchMock } = mockOAuthAuthorization();
    render(<McpHubPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "创建授权链接" }));
    const link = await screen.findByRole("link", { name: "打开授权页面" });
    const calls = fetchMock.mock.calls.length;
    vi.spyOn(Date, "now").mockReturnValue(session.expires_at * 1000 + 1);
    expect(fireEvent.click(link)).toBe(false);
    expect(screen.getByText("授权链接已过期")).toBeVisible();
    expect(screen.queryByRole("link", { name: "打开授权页面" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(calls);
  });

  it("never attaches an old URL to an authorization session created in another tab", async () => {
    const { session } = mockOAuthAuthorization();
    render(<McpHubPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "创建授权链接" }));
    await screen.findByRole("link", { name: "打开授权页面" });
    session.session_id = "other-tab-session";
    fireEvent.click(screen.getByRole("button", { name: "刷新授权状态" }));
    expect(await screen.findByText(/授权链接只在创建时返回/)).toBeVisible();
    expect(screen.queryByRole("link", { name: "打开授权页面" })).not.toBeInTheDocument();
  });

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
    expect(screen.getByText(/OAuth 工具仅在 V3 契约/)).toBeVisible();
    expect(screen.getByText(/OAuth Runtime：默认关闭/)).toBeVisible();
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
      oauth_discovery_available: true,
      registry_eligibility: "oauth_discovery_candidate",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({
          enabled: true,
          remote_enabled: false,
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
              eligibility: "oauth_discovery_candidate",
              remotes: [
                {
                  remote_id: "remote_1111111111111111",
                  transport: "streamable-http",
                  origin: "https://mcp.example.com",
                  eligibility: "oauth_discovery_candidate",
                  reason: "可进行 OAuth 元数据发现",
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

  it("binds, rotates, and revokes a fixed Registry Token without exposing target controls", async () => {
    const candidateId = "mcphub_" + "7".repeat(32);
    const bindingId = "mcpra_" + "8".repeat(32);
    let revision = 0;
    let revoked = false;
    const candidate = {
      candidate_id: candidateId,
      server_name: "io.example/token",
      version: "1.0.0",
      state: "draft",
      origin: "https://token.example.com",
      schema_digest: "",
      tools: [],
      connected: false,
      taint_reason: "",
      activation_eligible: false,
      activation_reason: "mcp_remote_auth_binding_missing",
      auth_required: true,
      auth_mode: "static_bearer",
      auth_header_name: "Authorization",
      auth_slot: "registry-secret-header",
      auth_policy_fingerprint: "a".repeat(64),
    };
    const authPayload = () => ({
      required: true,
      mode: "static_bearer",
      slot: "registry-secret-header",
      header_name: "Authorization",
      origin: "https://token.example.com",
      policy_fingerprint: "a".repeat(64),
      single_owner_warning: true,
      binding: revision > 0 && !revoked
        ? {
          binding_id: bindingId,
          revision,
          status: "active",
          masked_value: "tok••••last",
          display_name: "Example Token",
        }
        : null,
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({ enabled: true, remote_enabled: true, source: "registry", snapshot_at: 1, snapshot_count: 1 });
      }
      if (url.endsWith("/api/mcp/remote-auth/status")) {
        return json({
          enabled: true,
          static_token_enabled: true,
          single_owner_acknowledged: true,
          subject_mode: "local-single-owner",
          external_master_key_available: true,
          external_master_key_enforced: true,
          storage_ready: true,
          supported_auth_modes: ["static_bearer", "static_header"],
          multi_tenant: false,
        });
      }
      if (url.includes("/api/mcp/hub/servers?")) {
        return json({ items: [], total: 0, next_cursor: null, categories: [] });
      }
      if (url.endsWith("/api/mcp/hub/candidates")) return json({ items: [candidate] });
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/auth`) && !init?.method) {
        return json(authPayload());
      }
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/auth-bindings`) && init?.method === "POST") {
        revision = 1;
        return json(authPayload(), 201);
      }
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/auth-bindings/${bindingId}/rotate`) && init?.method === "POST") {
        revision = 2;
        return json(authPayload());
      }
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/auth-bindings/${bindingId}`) && init?.method === "DELETE") {
        revoked = true;
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    const card = (await screen.findByText("io.example/token")).closest("article");
    expect(card).not.toBeNull();
    expect(within(card!).getByText(/仅适用于本机可信运维者/)).toBeVisible();
    expect(within(card!).getByText(/Header：Authorization/)).toBeVisible();
    expect(within(card!).getByRole("button", { name: "安全预检" })).toBeDisabled();

    fireEvent.change(within(card!).getByLabelText("凭据显示名称"), { target: { value: "Example Token" } });
    fireEvent.change(within(card!).getByLabelText("绑定 io.example/token Token"), { target: { value: "first-secret" } });
    fireEvent.click(within(card!).getByRole("button", { name: "保存 Token" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url, init]) => (
        String(url).endsWith(`/api/mcp/hub/candidates/${candidateId}/auth-bindings`) &&
        (init as RequestInit | undefined)?.method === "POST"
      ));
      const body = JSON.parse(String((call?.[1] as RequestInit).body));
      expect(body).toEqual({
        slot: "registry-secret-header",
        display_name: "Example Token",
        secret: "first-secret",
      });
      expect(body).not.toHaveProperty("origin");
      expect(body).not.toHaveProperty("header_name");
      expect(body).not.toHaveProperty("tenant_id");
      expect(body).not.toHaveProperty("owner_id");
    });
    expect(await within(card!).findByText("revision 1")).toBeVisible();
    expect(within(card!).getByRole("button", { name: "安全预检" })).toBeEnabled();
    expect(screen.queryByDisplayValue("first-secret")).not.toBeInTheDocument();

    fireEvent.change(within(card!).getByLabelText("轮换 io.example/token Token"), { target: { value: "second-secret" } });
    fireEvent.click(within(card!).getByRole("button", { name: "轮换 Token" }));
    expect(await within(card!).findByText("revision 2")).toBeVisible();
    const rotateCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/rotate"));
    expect(JSON.parse(String((rotateCall?.[1] as RequestInit).body))).toEqual({
      secret: "second-secret",
      expected_revision: 1,
    });
    expect(screen.queryByDisplayValue("second-secret")).not.toBeInTheDocument();

    fireEvent.click(within(card!).getByRole("button", { name: "撤销 Token" }));
    const revokeDialog = await screen.findByRole("alertdialog", { name: "撤销访问 Token" });
    expect(fetchMock.mock.calls.some(([url, init]) => (
      String(url).endsWith(`/auth-bindings/${bindingId}`) &&
      (init as RequestInit | undefined)?.method === "DELETE"
    ))).toBe(false);
    fireEvent.click(within(revokeDialog).getByRole("button", { name: "撤销 Token" }));
    expect(await within(card!).findByRole("button", { name: "保存 Token" })).toBeDisabled();
    expect(within(card!).getByRole("button", { name: "安全预检" })).toBeDisabled();
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

  it("discovers OAuth metadata and registers only a public client without enabling runtime", async () => {
    const candidateId = "mcphub_" + "a".repeat(32);
    const sourceDigest = "b".repeat(64);
    const discoveryFingerprint = "c".repeat(64);
    let discovered = false;
    let registered = false;
    let authorizationPending = false;
    let tokenActive = false;
    let tokenRevision = 1;
    let remoteRevocationEnabled = false;
    const candidate = {
      candidate_id: candidateId,
      server_name: "io.example/oauth",
      version: "1.0.0",
      state: "draft",
      origin: "https://oauth-mcp.example.com",
      source_digest: sourceDigest,
      schema_digest: "",
      tools: [],
      connected: false,
      taint_reason: "",
      activation_eligible: false,
      activation_reason: "hub_preflight_required",
      auth_required: false,
      oauth_discovery_available: true,
      registry_eligibility: "oauth_discovery_candidate",
    };
    const oauthPayload = () => ({
      discovery: discovered ? {
        discovery_id: "mcpoauthdisc_" + "d".repeat(32),
        status: "active",
        discovery_fingerprint: discoveryFingerprint,
        resource_uri: "https://oauth-mcp.example.com/mcp",
        protected_resource_metadata_url: "https://oauth-mcp.example.com/.well-known/oauth-protected-resource/mcp",
        issuer: "https://auth.example.com/",
        authorization_endpoint: "https://auth.example.com/authorize",
        token_endpoint_origin: "https://auth.example.com",
        registration_endpoint_available: false,
        registration_endpoint: "",
        revocation_endpoint_available: true,
        pkce_method: "S256",
        scopes_supported: ["mcp:read"],
        policy_fingerprint: "e".repeat(64),
        scope_source: "protected_resource_metadata",
        recommended_scopes: ["mcp:read"],
        recommended_scope_digest: "2".repeat(64),
        offline_access_available: false,
        protocol_version: "2025-11-25",
      } : null,
      registration: registered ? {
        registration_id: "mcpoauthreg_" + "f".repeat(32),
        mode: "pre_registered",
        client_id: "operator-public-client",
        revision: 1,
        status: "active",
        discovery_fingerprint: discoveryFingerprint,
        registration_digest: "3".repeat(64),
      } : null,
      authorization_session: authorizationPending ? {
        session_id: "mcpoauthsession_" + "1".repeat(32),
        status: "pending",
        scopes: ["mcp:read"],
        scope_digest: "2".repeat(64),
        scope_source: "protected_resource_metadata",
        resource_bound: true,
        request_refresh_token: false,
        error_code: "",
        token_id: "",
        created_at: 1,
        expires_at: 9999999999,
      } : null,
      token: tokenActive ? {
        token_id: "mcpoauthtoken_" + "4".repeat(32),
        revision: tokenRevision,
        status: "active",
        scopes: ["mcp:read"],
        scope_digest: "2".repeat(64),
        scope_source: "protected_resource_metadata",
        resource_bound: true,
        protocol_version: "2025-11-25",
        expires_at: 9999999999,
        refresh_available: true,
        stored_encrypted: true,
      } : null,
      authorization_enabled: true,
      token_storage_enabled: true,
      review_enabled: true,
      runtime_enabled: false,
      remote_revocation_enabled: remoteRevocationEnabled,
      runtime_eligible: false,
      local_single_owner_warning: true,
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({ enabled: true, remote_enabled: true, source: "registry", snapshot_at: 1, snapshot_count: 1 });
      }
      if (url.endsWith("/api/mcp/remote-auth/oauth/status")) {
        return json({
          enabled: true,
          dynamic_registration_enabled: false,
          remote_auth_enabled: true,
          single_owner_acknowledged: true,
          external_master_key_available: true,
          external_master_key_enforced: true,
          storage_ready: true,
          client_metadata_document_configured: false,
          supported_registration_modes: ["pre_registered"],
          authorization_enabled: true,
          token_storage_enabled: true,
          review_enabled: true,
          runtime_enabled: false,
          remote_revocation_enabled: remoteRevocationEnabled,
          multi_tenant: false,
        });
      }
      if (url.includes("/api/mcp/hub/servers?")) return json({ items: [], total: 0, next_cursor: null, categories: [] });
      if (url.endsWith("/api/mcp/hub/candidates")) return json({ items: [candidate] });
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/oauth`) && !init?.method) return json(oauthPayload());
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/oauth/discover`) && init?.method === "POST") {
        discovered = true;
        return json(oauthPayload());
      }
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/oauth/registrations`) && init?.method === "POST") {
        registered = true;
        return json(oauthPayload(), 201);
      }
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/oauth/authorization-sessions`) && init?.method === "POST") {
        authorizationPending = true;
        return json({
          authorization_session: oauthPayload().authorization_session,
          authorization_url: "https://auth.example.com/authorize?state=server-owned",
        }, 201);
      }
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/oauth/tokens/mcpoauthtoken_${"4".repeat(32)}/refresh`) && init?.method === "POST") {
        tokenRevision += 1;
        return json(oauthPayload());
      }
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/oauth/tokens/mcpoauthtoken_${"4".repeat(32)}`) && init?.method === "DELETE") {
        tokenActive = false;
        return json({
          ...oauthPayload(),
          revocation: { local_revocation: "completed", remote_revocation: "unknown_outcome" },
        });
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    const card = (await screen.findByText("io.example/oauth")).closest("article");
    expect(card).not.toBeNull();
    expect(within(card!).getByText(/只有已发布 V3 契约/)).toBeVisible();
    expect(within(card!).getByRole("button", { name: "安全预检" })).toBeDisabled();
    fireEvent.click(within(card!).getByRole("button", { name: "检查并冻结 OAuth 元数据" }));

    expect(await within(card!).findByText(/Issuer：https:\/\/auth.example.com\//)).toBeVisible();
    expect(within(card!).getByText(/PKCE：S256/)).toBeVisible();
    expect(within(card!).getByText("远程撤销：支持标准 endpoint")).toBeVisible();
    expect(within(card!).getByRole("button", { name: "重新发现 OAuth 元数据" })).toBeVisible();
    expect(within(card!).getByRole("button", { name: "安全预检" })).toBeDisabled();
    expect(within(card!).getByRole("button", { name: "激活" })).toBeDisabled();
    const discoverCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/oauth/discover"));
    expect(JSON.parse(String((discoverCall?.[1] as RequestInit).body))).toEqual({
      expected_source_digest: sourceDigest,
    });

    fireEvent.change(within(card!).getByLabelText("io.example/oauth 预登记 public client ID"), {
      target: { value: "operator-public-client" },
    });
    fireEvent.click(within(card!).getByRole("button", { name: "登记已有 Client ID" }));
    expect(await within(card!).findByText(/Public client 已登记/)).toBeVisible();
    const registrationCall = fetchMock.mock.calls.find(([url, init]) => (
      String(url).endsWith("/oauth/registrations") && (init as RequestInit | undefined)?.method === "POST"
    ));
    expect(JSON.parse(String((registrationCall?.[1] as RequestInit).body))).toEqual({
      expected_discovery_fingerprint: discoveryFingerprint,
      mode: "pre_registered",
      client_id: "operator-public-client",
    });
    expect(JSON.stringify(JSON.parse(String((registrationCall?.[1] as RequestInit).body)))).not.toContain("client_secret");
    fireEvent.click(within(card!).getByRole("button", { name: "创建授权链接" }));
    const authorizationLink = await within(card!).findByRole("link", { name: "打开授权页面" });
    expect(authorizationLink).toHaveAttribute("href", "https://auth.example.com/authorize?state=server-owned");
    expect(authorizationLink.getAttribute("rel")).toContain("noreferrer");
    const authorizationCall = fetchMock.mock.calls.find(([url, init]) => (
      String(url).endsWith("/oauth/authorization-sessions") && (init as RequestInit | undefined)?.method === "POST"
    ));
    expect(JSON.parse(String((authorizationCall?.[1] as RequestInit).body))).toEqual({
      expected_discovery_fingerprint: discoveryFingerprint,
      expected_registration_digest: "3".repeat(64),
      expected_scope_digest: "2".repeat(64),
      request_refresh_token: false,
    });
    expect(String((authorizationCall?.[1] as RequestInit).body)).not.toMatch(/code|verifier|token_endpoint|client_id/);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("callback"))).toBe(false);
    const oauthSummaryCalls = () => fetchMock.mock.calls.filter(([url, init]) => (
      String(url).endsWith(`/api/mcp/hub/candidates/${candidateId}/oauth`) && !init?.method
    )).length;
    const beforeRefresh = oauthSummaryCalls();
    fireEvent.click(within(card!).getByRole("button", { name: "刷新授权状态" }));
    await waitFor(() => expect(oauthSummaryCalls()).toBeGreaterThan(beforeRefresh));
    expect(await screen.findByRole("status")).toHaveTextContent("io.example/oauth 的 OAuth 授权状态已刷新");

    tokenActive = true;
    remoteRevocationEnabled = true;
    fireEvent.click(within(card!).getByRole("button", { name: "刷新授权状态" }));
    expect(await within(card!).findByText(/revision 1/)).toBeVisible();
    expect(within(card!).queryByRole("link", { name: "打开授权页面" })).not.toBeInTheDocument();
    fireEvent.click(within(card!).getByRole("button", { name: "刷新 Token" }));
    const refreshDialog = await screen.findByRole("alertdialog", { name: "刷新 OAuth Token" });
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/refresh"))).toBe(false);
    fireEvent.click(within(refreshDialog).getByRole("button", { name: "刷新 Token" }));
    expect(await within(card!).findByText(/revision 2/)).toBeVisible();
    const refreshCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/refresh"));
    expect(JSON.parse(String((refreshCall?.[1] as RequestInit).body))).toEqual({ expected_revision: 1 });

    fireEvent.click(within(card!).getByRole("button", { name: "撤销 Token" }));
    const revokeDialog = await screen.findByRole("alertdialog", { name: "撤销 OAuth Token" });
    expect(within(revokeDialog).getByText(/RFC 7009.*不可逆撤销/)).toBeVisible();
    fireEvent.click(within(revokeDialog).getByRole("button", { name: "撤销 Token" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "io.example/oauth 的本地 Token 已撤销；远程结果未知，不会自动重试",
    );
    const revokeCall = fetchMock.mock.calls.find(([url, init]) => (
      String(url).includes("/oauth/tokens/mcpoauthtoken_") &&
      (init as RequestInit | undefined)?.method === "DELETE"
    ));
    expect(revokeCall).toBeTruthy();
    expect((revokeCall?.[1] as RequestInit).body).toBeUndefined();
    expect(within(card!).queryByRole("button", { name: "撤销 Token" })).not.toBeInTheDocument();
  });

  it("keeps OAuth summary failures visible with their fixed error code", async () => {
    const candidateId = "mcphub_" + "e".repeat(32);
    const candidate = {
      candidate_id: candidateId,
      server_name: "io.example/oauth-summary",
      version: "1.0.0",
      state: "draft",
      origin: "https://oauth-summary.example.com",
      source_digest: "a".repeat(64),
      schema_digest: "",
      tools: [],
      connected: false,
      taint_reason: "",
      activation_eligible: false,
      activation_reason: "hub_preflight_required",
      auth_required: false,
      oauth_discovery_available: true,
      registry_eligibility: "oauth_discovery_candidate",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({ enabled: true, remote_enabled: false, source: "registry", snapshot_at: 1, snapshot_count: 1 });
      }
      if (url.endsWith("/api/mcp/remote-auth/oauth/status")) {
        return json({
          enabled: true,
          dynamic_registration_enabled: false,
          remote_auth_enabled: true,
          single_owner_acknowledged: true,
          external_master_key_available: true,
          external_master_key_enforced: true,
          storage_ready: true,
          client_metadata_document_configured: false,
          supported_registration_modes: ["pre_registered"],
          authorization_enabled: false,
          token_storage_enabled: false,
          multi_tenant: false,
        });
      }
      if (url.includes("/api/mcp/hub/servers?")) return json({ items: [], total: 0, next_cursor: null, categories: [] });
      if (url.endsWith("/api/mcp/hub/candidates")) return json({ items: [candidate] });
      if (url.endsWith(`/api/mcp/hub/candidates/${candidateId}/oauth`)) {
        return json({
          detail: {
            code: "mcp_remote_oauth_discovery_stale",
            error: "OAuth 发现快照已过期或发生漂移。",
          },
        }, 409);
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    const card = (await screen.findByText("io.example/oauth-summary")).closest("article");
    expect(card).not.toBeNull();
    expect(await within(card!).findByRole("alert")).toHaveTextContent("OAuth 发现快照已过期");
    expect(within(card!).getByRole("alert")).toHaveTextContent("错误码：mcp_remote_oauth_discovery_stale");
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
    let preflightFailed = false;
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
      if (url.endsWith("/api/mcp/hub/candidates") && !init?.method) {
        return json({
          items: [{
            ...candidate,
            state: preflightFailed ? "blocked" : candidate.state,
            taint_reason: preflightFailed ? "hub_dns_private_or_synthetic_denied" : "",
          }],
        });
      }
      if (url.endsWith(`/api/mcp/hub/candidates/${candidate.candidate_id}/preflight`)) {
        preflightFailed = true;
        return json({
          detail: {
            code: "hub_dns_private_or_synthetic_denied",
            error: "远程 Schema 校验失败",
          },
        }, 409);
      }
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "安全预检" }));
    const candidateCard = screen.getByText("io.example/public").closest("article");
    expect(candidateCard).not.toBeNull();
    expect(await within(candidateCard!).findByRole("alert")).toHaveTextContent("远程 Schema 校验失败");
    expect(within(candidateCard!).getByRole("alert")).toHaveTextContent("错误码：hub_dns_private_or_synthetic_denied");
    expect(within(candidateCard!).getByText(/目标解析到了私网或合成地址/)).toBeVisible();
    expect(within(candidateCard!).getByText(/错误码：hub_dns_private_or_synthetic_denied/)).toBeVisible();
    expect(within(candidateCard!).getByRole("button", { name: "安全预检" })).toBeEnabled();
  });

  it("converges a revoked candidate to one non-actionable user state", async () => {
    const candidate = {
      candidate_id: "mcphub_" + "5".repeat(32),
      server_name: "io.example/revoked",
      version: "1.0.0",
      state: "verified",
      origin: "https://revoked.example.com",
      schema_digest: "a".repeat(64),
      tools: [{ name: "upstream_tool", description: "", schema_digest: "b".repeat(64) }],
      connected: false,
      taint_reason: "",
      activation_eligible: false,
      activation_reason: "hub_contract_revoked",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/hub/status")) {
        return json({ enabled: true, remote_enabled: true, source: "registry", snapshot_at: 1, snapshot_count: 0 });
      }
      if (url.includes("/api/mcp/hub/servers?")) return json({ items: [], total: 0, next_cursor: null, categories: [] });
      if (url.endsWith("/api/mcp/hub/candidates")) return json({ items: [candidate] });
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    const candidateCard = (await screen.findByText("io.example/revoked")).closest("article");
    expect(candidateCard).not.toBeNull();
    expect(within(candidateCard!).getByText("状态：已撤销 · 未连接")).toBeVisible();
    expect(within(candidateCard!).getByRole("button", { name: "安全预检" })).toBeDisabled();
    expect(within(candidateCard!).getByRole("button", { name: "激活" })).toBeDisabled();
    expect(within(candidateCard!).queryByText("upstream_tool")).not.toBeInTheDocument();
    expect(within(candidateCard!).getByText(/执行契约已由本地运维者撤销/)).toBeVisible();
  });

  it("confirms candidate deletion and reports the completed action", async () => {
    let deleted = false;
    const candidate = {
      candidate_id: "mcphub_" + "6".repeat(32),
      server_name: "io.example/delete-me",
      version: "1.0.0",
      state: "draft",
      origin: "https://delete.example.com",
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
      if (url.endsWith(`/api/mcp/hub/candidates/${candidate.candidate_id}`) && init?.method === "DELETE") {
        deleted = true;
        return json({ ok: true });
      }
      if (url.endsWith("/api/mcp/hub/candidates")) return json({ items: deleted ? [] : [candidate] });
      throw new Error(`unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<McpHubPanel />);

    const deleteButton = await screen.findByRole("button", { name: "删除 Hub 候选 io.example/delete-me" });
    fireEvent.click(deleteButton);
    const firstDialog = await screen.findByRole("alertdialog", { name: "删除 Hub 连接" });
    expect(within(firstDialog).getByText(/io\.example\/delete-me/)).toBeVisible();
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith(candidate.candidate_id) && (init as RequestInit | undefined)?.method === "DELETE")).toBe(false);
    fireEvent.click(within(firstDialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("alertdialog", { name: "删除 Hub 连接" })).not.toBeInTheDocument();

    fireEvent.click(deleteButton);
    const secondDialog = await screen.findByRole("alertdialog", { name: "删除 Hub 连接" });
    fireEvent.click(within(secondDialog).getByRole("button", { name: "删除连接" }));
    expect(await screen.findByRole("status")).toHaveTextContent("io.example/delete-me 已从“我的 Hub 连接”删除");
    expect(screen.queryByText("io.example/delete-me")).not.toBeInTheDocument();
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
          oauth_review_enabled: false,
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
