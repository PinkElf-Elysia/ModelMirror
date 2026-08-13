import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mcpProjects } from "../data/mcpProjects";
import McpServerCard from "./McpServerCard";

function project(projectId: string) {
  const match = mcpProjects.find((item) => item.id === projectId);
  if (!match) throw new Error(`Missing MCP fixture: ${projectId}`);
  return match;
}

describe("McpServerCard Playwright sample", () => {
  it("uses the same ready-card structure while preserving Playwright copy", () => {
    render(<McpServerCard project={project("playwright-mcp")} />);

    const heading = screen.getByRole("heading", { name: "Playwright MCP" });
    expect(heading).toBeVisible();
    const card = heading.closest("article");
    expect(card).toHaveClass("border-white/10", "bg-ink-950/78");
    expect(within(card as HTMLElement).getByText("浏览器与网页")).toBeVisible();
    expect(within(card as HTMLElement).getByText("可用")).toBeVisible();
    expect(
      screen.getByText("打开网页、读取页面并执行受控点击、填写与截图。"),
    ).toBeVisible();
    expect(screen.getByText("主要能力")).toBeVisible();
    expect(screen.getByText("打开网页")).toBeVisible();
    expect(screen.getByText("读取页面")).toBeVisible();
    expect(screen.getByText("点击与填写")).toBeVisible();
    expect(screen.getByText("生成截图")).toBeVisible();
    expect(
      screen.getByText("临时匿名浏览器，不保留登录态；不支持上传和下载。"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "打开工具台" })).toBeVisible();
    expect(screen.getByRole("button", { name: "配置与使用" })).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "Playwright MCP" })).not.toBeInTheDocument();

    expect(screen.queryByText("README 摘要")).not.toBeInTheDocument();
    expect(screen.queryByText("资料核验")).not.toBeInTheDocument();
    expect(screen.queryByText("适配批次")).not.toBeInTheDocument();
    expect(screen.queryByText("风险等级")).not.toBeInTheDocument();
    expect(screen.queryByText("本批生产验收门槛")).not.toBeInTheDocument();
    expect(screen.queryByText("已验证运行隔离")).not.toBeInTheDocument();
    expect(screen.queryByText("受控连接")).not.toBeInTheDocument();
  });

  it("extends the user-facing summary to other ready cards", () => {
    render(<McpServerCard project={project("chrome-devtools-mcp")} />);

    expect(screen.getByRole("heading", { name: "Chrome DevTools MCP" })).toBeVisible();
    expect(screen.getByText("主要能力")).toBeVisible();
    expect(screen.getByRole("button", { name: "打开工具台" })).toBeVisible();
    expect(screen.queryByText("README 摘要")).not.toBeInTheDocument();
    expect(screen.queryByText("资料核验")).not.toBeInTheDocument();
    expect(screen.queryByText("本批生产验收门槛")).not.toBeInTheDocument();
  });

  it.each([
    [
      "takashiishida-arxiv-latex-mcp",
      "Arxiv Latex Mcp",
      "读取 arXiv 论文摘要、章节结构和 LaTeX 正文。",
      "论文摘要",
    ],
    [
      "greptimeteam-greptimedb-mcp-server",
      "GreptimeDB",
      "查询固定 GreptimeDB 数据表的结构、时间范围和健康状态。",
      "时序查询",
    ],
    [
      "victoriametrics-community-mcp-victoriametrics",
      "Mcp Victoriametrics",
      "读取 VictoriaMetrics 指标、标签和限定时间范围的监控数据。",
      "指标列表",
    ],
  ])("gives newly adapted %s concise ready copy", (projectId, name, description, capability) => {
    render(<McpServerCard project={project(projectId)} />);

    expect(screen.getByRole("heading", { name })).toBeVisible();
    expect(screen.getByText(description)).toBeVisible();
    expect(screen.getByText(capability)).toBeVisible();
    expect(screen.getByText("可用")).toBeVisible();
    expect(screen.queryByText(/当前判定为 ready/)).not.toBeInTheDocument();
  });

  it("opens the ready tool workbench as an overlay and restores focus", async () => {
    const user = userEvent.setup();
    render(<McpServerCard project={project("chrome-devtools-mcp")} />);

    const trigger = screen.getByRole("button", { name: "打开工具台" });
    await user.click(trigger);

    expect(screen.getByRole("dialog", { name: "Chrome DevTools MCP" })).toBeVisible();
    expect(screen.getAllByRole("heading", { name: "Chrome DevTools MCP" })).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "关闭工具台" }));
    expect(screen.queryByRole("dialog", { name: "Chrome DevTools MCP" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("keeps unavailable cards explicit while removing development details", () => {
    render(<McpServerCard project={project("airbnb-mcp")} />);

    const heading = screen.getByRole("heading", { name: /Airbnb/i });
    expect(heading).toBeVisible();
    expect(heading.closest("article")).toHaveClass("h-full");
    expect(heading.closest("article")).not.toHaveClass("self-start");
    expect(screen.getByText("未适配", { selector: "p" })).toBeVisible();
    expect(screen.getByText("上游接口不稳定，恢复兼容后再开放。")).toBeVisible();
    expect(screen.getByText("需要远程传输适配")).toBeVisible();
    expect(screen.getByText("需要系统权限")).toBeVisible();
    expect(screen.getByRole("button", { name: "未适配" })).toBeDisabled();
    expect(screen.queryByText("README 摘要")).not.toBeInTheDocument();
    expect(screen.queryByText("资料核验")).not.toBeInTheDocument();
    expect(screen.queryByText("适配批次")).not.toBeInTheDocument();
    expect(screen.queryByText("本批生产验收门槛")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "配置与使用" })).not.toBeInTheDocument();
  });
});
