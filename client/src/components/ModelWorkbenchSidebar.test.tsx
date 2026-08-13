import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import ModelWorkbenchSidebar from "./ModelWorkbenchSidebar";

const expectedEntries = [
  ["自定义工作流", "/workflow", "拖拽节点，编排并运行任务"],
  ["RAG 知识库", "/rag", "上传资料，检索并用于问答"],
  ["Coding", "/coding", "连接项目，完成代码任务"],
  ["系统设置", "/settings", "管理连接、网关与功能开关"],
] as const;

function renderSidebar(compact = false) {
  return render(
    <MemoryRouter>
      <ModelWorkbenchSidebar compact={compact} />
    </MemoryRouter>,
  );
}

describe("ModelWorkbenchSidebar", () => {
  it("shows the four real workbench destinations in the approved order", () => {
    renderSidebar();

    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(4);

    expectedEntries.forEach(([name, href, description], index) => {
      expect(links[index]).toHaveAttribute("href", href);
      expect(links[index]).toHaveTextContent(name);
      expect(links[index]).toHaveTextContent(description);
    });

    expect(screen.queryByText("已完成适配")).not.toBeInTheDocument();
    expect(screen.queryByText("模镜服务台")).not.toBeInTheDocument();
    expect(screen.queryByText("元智能体 Beta")).not.toBeInTheDocument();
  });

  it("keeps the mobile workbench entry collapsed by default", () => {
    const { container } = renderSidebar(true);

    const details = container.querySelector("details");
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByText("4 个入口")).toBeInTheDocument();
  });
});
