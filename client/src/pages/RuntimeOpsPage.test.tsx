import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RuntimeOpsPage from "./RuntimeOpsPage";

const environmentSummary = {
  git_available: true,
  llm_gateway_configured: true,
  model_gateway_ready: true,
  node_available: true,
  npm_available: true,
  npx_available: true,
  openrouter_configured: true,
  python_available: true,
  redacted: true,
  updated_at: 1_723_500_000,
};

function responseFor(url: string) {
  let payload: unknown = [];
  if (url === "/api/mcp/sessions") payload = { sessions: [] };
  if (url === "/api/registry/tools") payload = { tools: [] };
  if (url === "/api/skills/installed") payload = { skills: [] };
  if (url === "/api/runtime/environment-summary") payload = environmentSummary;
  if (url === "/api/runtime/client-hosts") payload = { hosts: [] };

  return Promise.resolve({
    json: () => Promise.resolve(payload),
    ok: true,
  } as Response);
}

function renderPage() {
  return render(
    <MemoryRouter>
      <RuntimeOpsPage />
    </MemoryRouter>,
  );
}

describe("RuntimeOpsPage diagnostic-first shell", () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => responseFor(String(input)));

  beforeEach(() => {
    fetchMock.mockClear();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("puts health and run diagnostics before client pairing with one global refresh", async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "刷新全部" })).toBeEnabled(),
    );

    expect(screen.getByRole("heading", { name: "Runtime 运维" })).toBeInTheDocument();
    expect(screen.getByLabelText("运行健康摘要")).toBeInTheDocument();
    ["运行状态", "MCP 连接", "客户端宿主", "环境依赖"].forEach((label) =>
      expect(screen.getAllByText(label).length).toBeGreaterThan(0),
    );

    const runHeading = screen.getByRole("heading", { name: "运行记录" });
    const clientHeading = screen.getByRole("heading", { name: "客户端宿主" });
    expect(
      runHeading.compareDocumentPosition(clientHeading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    expect(screen.getAllByRole("button", { name: "刷新全部" })).toHaveLength(1);
    expect(screen.queryByText("应用并刷新")).not.toBeInTheDocument();
    expect(screen.queryByText("重试待接入")).not.toBeInTheDocument();
    expect(screen.queryByText("重试能力待接入")).not.toBeInTheDocument();
  });

  it("refreshes only run records when the run filter changes", async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "刷新全部" })).toBeEnabled(),
    );
    fetchMock.mockClear();

    fireEvent.change(screen.getByLabelText("运行类型"), {
      target: { value: "workflow" },
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/runtime/runs?limit=20&run_type=workflow",
    );
  });

  it("keeps client pairing actions compact and moves setup links into one help row", async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "配对 Chrome" })).toBeEnabled(),
    );

    expect(screen.getByRole("button", { name: "配对 Office" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "下载 Chrome 扩展" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载 Office Manifest" })).toBeInTheDocument();
    expect(screen.getAllByText("首次使用")).toHaveLength(1);
  });

  it("shows one runtime resource view at a time with pressed-button semantics", async () => {
    renderPage();

    const mcpTab = await screen.findByRole("button", { name: "MCP 连接 0" });
    const toolsTab = screen.getByRole("button", { name: "工具 0" });
    const skillsTab = screen.getByRole("button", { name: "Skill 0" });
    const environmentTab = screen.getByRole("button", { name: "环境依赖 8/8" });

    expect(mcpTab).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("没有符合条件的 MCP 连接")).toBeInTheDocument();

    fireEvent.click(toolsTab);
    expect(toolsTab).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("当前没有注册工具")).toBeInTheDocument();
    expect(screen.queryByText("没有符合条件的 MCP 连接")).not.toBeInTheDocument();

    fireEvent.click(skillsTab);
    expect(screen.getByText("暂无已安装 Skill")).toBeInTheDocument();

    fireEvent.click(environmentTab);
    expect(screen.getByText("模型网关")).toBeInTheDocument();
    expect(screen.getByText("python")).toBeInTheDocument();
  });

  it("uses localized run states and offers a reversible filter reset", async () => {
    renderPage();

    const statusSelect = await screen.findByLabelText("运行状态");
    const clearButton = screen.getByRole("button", { name: "清除筛选" });

    ["等待中", "运行中", "已完成", "失败", "已取消"].forEach((label) =>
      expect(screen.getByRole("option", { name: label })).toBeInTheDocument(),
    );
    expect(clearButton).toBeDisabled();

    fireEvent.change(statusSelect, { target: { value: "failed" } });
    expect(clearButton).toBeEnabled();
    fireEvent.click(clearButton);

    expect(statusSelect).toHaveValue("all");
    expect(clearButton).toBeDisabled();
  });
});
