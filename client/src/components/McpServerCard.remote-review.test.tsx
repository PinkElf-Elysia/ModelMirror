import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { McpCatalogAdapterStatus } from "../data/mcpAdaptationPlan";
import { mcpProjects } from "../data/mcpProjects";
import McpServerCard from "./McpServerCard";

vi.mock("./McpCatalogRemotePanel", () => ({
  default: ({ projectId }: { projectId: string }) => (
    <section aria-label="远程复核测试面板">{projectId}</section>
  ),
}));

vi.mock("./McpCredentialPanel", () => ({
  default: ({ projectId }: { projectId: string }) => (
    <section aria-label="远程凭据测试面板">{projectId}</section>
  ),
}));

function project(projectId: string) {
  const match = mcpProjects.find((item) => item.id === projectId);
  if (!match) throw new Error(`Missing MCP fixture: ${projectId}`);
  return match;
}

function remoteStatus(enabled: boolean): McpCatalogAdapterStatus {
  return {
    project_id: "github-mcp-server",
    wave: 22,
    availability: "planned",
    connection_kind: "remote-mcp",
    risk: "high",
    required_capabilities: [],
    limitations: [],
    feature_enabled: false,
    executable: false,
    connected: false,
    session_id: null,
    allowed_settings: [],
    credential_slots: ["github_token"],
    setting_fields: [],
    credential_fields: [
      {
        key: "github_token",
        label: "GitHub Token",
        description: "保存到本地加密槽",
        required: true,
        accepted_kinds: ["api-key"],
      },
    ],
    configured: false,
    configured_settings: [],
    configured_credential_slots: [],
    configuration_values: {},
    credential_bindings: {},
    credential_verification: "unverified",
    adapter_version: "github/github-mcp-server@v1.6.0+remote-repos-readonly",
    runtime_image: "",
    network_policy: "fixed-origin",
    filesystem_policy: "none",
    resource_limits: {},
    workspace_policy: null,
    database_policy: null,
    remote_auth_mode: "static_bearer",
    remote_review_capable: true,
    remote_review_credential_ready: true,
    remote_review_enabled: enabled,
    preflight_status: "not-applicable",
    tool_policies: {},
  };
}

describe("McpServerCard remote review entry", () => {
  it("opens the review panel without making a planned project executable", async () => {
    const user = userEvent.setup();
    render(
      <McpServerCard
        adapterStatus={remoteStatus(true)}
        project={project("github-mcp-server")}
      />,
    );

    expect(screen.getByText("待复核", { selector: "p" })).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const trigger = screen.getByRole("button", { name: "认证与复核" });
    expect(trigger).toBeEnabled();

    await user.click(trigger);

    const dialog = screen.getByRole("dialog", {
      name: "GitHub MCP Server · 认证与复核",
    });
    expect(dialog).toBeVisible();
    expect(dialog.parentElement).toBe(document.body);
    expect(screen.getByLabelText("远程复核测试面板")).toHaveTextContent(
      "github-mcp-server",
    );
    expect(screen.getByLabelText("远程凭据测试面板")).toHaveTextContent(
      "github-mcp-server",
    );
    expect(screen.getByText(/R4A 不会激活或暴露 Runtime 工具/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /连接 Server/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关闭认证与复核" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps the entry disabled when the unification gate is off", () => {
    render(
      <McpServerCard
        adapterStatus={remoteStatus(false)}
        project={project("github-mcp-server")}
      />,
    );

    expect(screen.getByRole("button", { name: "未适配" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "认证与复核" })).not.toBeInTheDocument();
  });
});
