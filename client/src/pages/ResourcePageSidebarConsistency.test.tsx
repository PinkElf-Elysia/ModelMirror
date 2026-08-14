import { render, screen } from "@testing-library/react";
import { type ReactElement, type ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import RuntimeOpsPage from "./RuntimeOpsPage";
import StudioHomePage from "./StudioHomePage";

vi.mock("../components/PageContainer", () => ({
  default: ({
    mobileSidebar,
    showSystemCapabilityBar = true,
    sidebar,
  }: {
    mobileSidebar?: ReactNode;
    showSystemCapabilityBar?: boolean;
    sidebar?: ReactNode;
  }) => (
    <>
      <div data-testid="desktop-sidebar">{sidebar}</div>
      <div data-testid="mobile-sidebar">{mobileSidebar}</div>
      {showSystemCapabilityBar ? <div>原服务台</div> : null}
    </>
  ),
}));

function renderPage(Page: () => ReactElement) {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
  return render(
    <MemoryRouter>
      <Page />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe.each([
  ["Runtime", RuntimeOpsPage],
  ["Studio", StudioHomePage],
])("%s resource sidebar", (_label, Page) => {
  it("reuses the shared workbench entry on desktop and mobile", () => {
    renderPage(Page);

    expect(screen.getAllByText("工作台入口")).toHaveLength(2);
    ["自定义工作流", "RAG 知识库", "Coding", "系统设置"].forEach(
      (entry) => expect(screen.getAllByText(entry)).toHaveLength(2),
    );
    expect(screen.queryByText("Runtime Ops")).not.toBeInTheDocument();
    expect(screen.queryByText("工作空间")).not.toBeInTheDocument();
    expect(screen.queryByText("原服务台")).not.toBeInTheDocument();
  });
});
