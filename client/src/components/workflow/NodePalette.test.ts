import { createElement } from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NodePalette, { disabledPaletteItem } from "./NodePalette";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NodePalette disabled workflow nodes", () => {
  it("keeps a disabled node visible with its explicit reason", () => {
    const placeholder = disabledPaletteItem({
      kind: "document_extractor",
      icon: "DOC",
      title: "文档提取器",
      description: "从文件资产提取文本。",
      enabled: false,
      metadata: { status_reason: "Workflow 文件资产变量当前未启用。" },
    });

    expect(placeholder.enabled).toBe(false);
    expect(placeholder.statusLabel).toBe("默认关闭");
    expect(placeholder.description).toContain("Workflow 文件资产变量当前未启用");
  });

  it("renders a disabled registry node as a non-draggable placeholder", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input) === "/api/runtime/middleware-nodes") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        return new Response(
          JSON.stringify({
            version: "test",
            tabs: [
              { id: "workflow", label: "工作流" },
              { id: "knowledge", label: "知识流水线" },
            ],
            sections: [
              {
                id: "transform",
                label: "转换",
                description: "转换节点",
                tab: "workflow",
                items: [
                  {
                    kind: "document_extractor",
                    icon: "DOC",
                    title: "文档提取器",
                    description: "从文件资产提取文本。",
                    enabled: false,
                    metadata: {
                      status_reason: "Workflow 文件资产变量当前未启用。",
                    },
                  },
                ],
                placeholders: [],
              },
            ],
            knowledge_pipeline: { items: [], placeholders: [] },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );

    render(createElement(NodePalette));

    const reason = await screen.findByText(
      /Workflow 文件资产变量当前未启用。/,
    );
    const placeholder = reason.closest('[aria-disabled="true"]');
    expect(placeholder).not.toBeNull();
    expect(placeholder).not.toHaveAttribute("draggable");
    expect(
      screen.queryByRole("button", { name: /文档提取器/ }),
    ).not.toBeInTheDocument();
  });
});
