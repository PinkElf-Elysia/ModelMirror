import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mcpProjects } from "../data/mcpProjects";
import McpBrowserPage, { prioritizeReadyProjects } from "./McpBrowserPage";

vi.mock("../components/McpServerCard", () => ({
  default: ({ project }: { project: { name: string } }) => (
    <article data-testid="mcp-server-card">{project.name}</article>
  ),
}));

vi.mock("../components/McpHubPanel", () => ({
  default: () => <div data-testid="mcp-hub-panel">Hub panel</div>,
}));

function LocationProbe() {
  return <output data-testid="location-search">{useLocation().search}</output>;
}

function response(payload: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    }),
  );
}

function renderPage(
  adapters: Array<Record<string, unknown>> = [],
  sessions: Array<Record<string, unknown>> = [],
  initialEntry = "/mcps",
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/sessions")) return response({ sessions });
      if (url.endsWith("/api/registry/tools")) return response({ tools: [] });
      if (url.endsWith("/api/mcp/catalog/adapters")) {
        return response({ adapters });
      }
      throw new Error(`unexpected URL: ${url}`);
    }),
  );

  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <McpBrowserPage />
      <LocationProbe />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("McpBrowserPage first-screen shell", () => {
  it("puts ready tools first only in the unfiltered all view", async () => {
    const projects = [
      { id: "blocked-a", availability: "blocked" },
      { id: "ready-a", availability: "ready" },
      { id: "planned-a", availability: "planned" },
      { id: "ready-b", availability: "ready" },
    ];

    expect(
      prioritizeReadyProjects(projects, (project) => project.availability, true).map(
        (project) => project.id,
      ),
    ).toEqual(["ready-a", "ready-b", "blocked-a", "planned-a"]);
    expect(
      prioritizeReadyProjects(projects, (project) => project.availability, false).map(
        (project) => project.id,
      ),
    ).toEqual(projects.map((project) => project.id));
  });

  it("keeps only the approved compact procurement information", async () => {
    renderPage([], [{ session_id: "session-1" }]);

    expect(
      screen.getByRole("heading", { level: 1, name: "MCP 工具采购" }),
    ).toBeVisible();
    expect(
      screen.getByText("安装 MCP 服务，为 AI 扩展更多工具能力"),
    ).toBeVisible();
    expect(screen.getByText("个工具")).toBeVisible();
    expect(screen.getByText("可用")).toBeVisible();
    expect(screen.getByText("分类")).toBeVisible();
    expect(screen.getByText("已连接")).toBeVisible();
    expect(screen.getByRole("tab", { name: "工具货架" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "已连接注册表" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "MCP Hub" })).toBeVisible();
    expect(
      screen.getByRole("searchbox", { name: "搜索 MCP 工具" }),
    ).toHaveAttribute("placeholder", "搜索名称、用途或标签");
    expect(
      screen.getByRole("heading", { name: "已上架工具箱" }),
    ).toBeVisible();

    expect(screen.queryByText("中文 MCP 工具目录")).not.toBeInTheDocument();
    expect(screen.queryByText("采购台状态")).not.toBeInTheDocument();
    expect(screen.queryByText("全局工具数")).not.toBeInTheDocument();
    expect(screen.queryByText("运行态")).not.toBeInTheDocument();
    expect(screen.queryByText("搜索工具")).not.toBeInTheDocument();
    expect(screen.queryByText(/待适配.*不代表/)).not.toBeInTheDocument();

    await waitFor(() => expect(screen.getByText("1")).toBeVisible());
  });

  it("reuses the approved workbench sidebar", () => {
    renderPage();

    expect(screen.getAllByText("工作台入口").length).toBeGreaterThan(0);
    ["自定义工作流", "RAG 知识库", "Coding", "系统设置"].forEach(
      (entry) => expect(screen.getAllByText(entry).length).toBeGreaterThan(0),
    );
    expect(screen.queryByText("工具采购清单")).not.toBeInTheDocument();
  });

  it("keeps the selected MCP view in the URL across reloads", async () => {
    const firstRender = renderPage();

    fireEvent.click(screen.getByRole("tab", { name: "MCP Hub" }));
    expect(screen.getByRole("tab", { name: "MCP Hub" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("location-search")).toHaveTextContent("?view=hub");
    expect(screen.getByTestId("mcp-hub-panel")).toBeVisible();

    firstRender.unmount();
    renderPage([], [], "/mcps?view=hub");
    expect(screen.getByRole("tab", { name: "MCP Hub" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("mcp-hub-panel")).toBeVisible();
  });

  it("keeps the all filter visible and toggles wrapped secondary categories", () => {
    renderPage();

    expect(screen.getByRole("button", { name: "全部 · 300" })).toBeVisible();
    const primaryGroup = screen.getByRole("group", { name: "按工具类别筛选" });
    expect(within(primaryGroup).getAllByRole("button")).toHaveLength(4);
    expect(primaryGroup.parentElement).not.toHaveClass("overflow-x-auto");
    expect(
      screen.queryByRole("group", { name: "更多 MCP 工具类别" }),
    ).not.toBeInTheDocument();

    const toggle = within(primaryGroup).getByRole("button", {
      name: "更多分类",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);

    const expandedGroup = screen.getByRole("group", {
      name: "更多 MCP 工具类别",
    });
    expect(expandedGroup).toBeVisible();
    expect(expandedGroup).not.toHaveClass("overflow-x-auto");
    expect(
      within(primaryGroup).getByRole("button", { name: "收起分类" }),
    ).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(
      within(primaryGroup).getByRole("button", { name: "收起分类" }),
    );
    expect(
      screen.queryByRole("group", { name: "更多 MCP 工具类别" }),
    ).not.toBeInTheDocument();
  });

  it("aggregates planned and adapting projects under the single 适配中 filter", async () => {
    const adapters = [
      ...mcpProjects.map((project) => ({
        project_id: project.id,
        availability: "blocked",
      })),
      { project_id: "playwright-mcp", availability: "planned" },
      { project_id: "github-mcp-server", availability: "adapting" },
    ];
    renderPage(adapters);

    const statusGroup = screen.getByRole("group", {
      name: "按 MCP 适配状态筛选",
    });
    const inProgress = await within(statusGroup).findByRole("button", {
      name: /适配中 · 2/,
    });
    fireEvent.click(inProgress);

    expect(inProgress).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(screen.getByText("匹配 2")).toBeVisible());
    expect(screen.queryByText("已排期、待适配")).not.toBeInTheDocument();
    expect(screen.queryByText("适配受阻")).not.toBeInTheDocument();
  });
});
