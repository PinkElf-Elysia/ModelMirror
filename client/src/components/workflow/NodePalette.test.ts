import { createElement } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NodePalette, { disabledPaletteItem } from "./NodePalette";
import {
  knowledgePipelineItems,
  knowledgePipelinePlaceholders,
} from "./workflowNodeRegistry";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NodePalette disabled workflow nodes", () => {
  it("only exposes executable knowledge consumption nodes", () => {
    expect(knowledgePipelineItems.map((item) => item.kind)).toEqual([
      "knowledge_base",
      "knowledge_retrieval",
      "vision_understanding",
    ]);
    expect(knowledgePipelineItems.every((item) => item.enabled !== false)).toBe(true);
    expect(knowledgePipelinePlaceholders).toEqual([]);
  });

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
            version: "xpert-workflow-node-registry-v4",
            contract_version: 3,
            contract_checksum: "a".repeat(64),
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
                    kind: "input",
                    icon: "IN",
                    title: "触发器",
                    description: "定义工作流输入。",
                    enabled: true,
                    contract: {
                      kind: "input",
                      contract_status: "complete",
                      config_schema: {},
                      ports: [],
                      edge: {},
                      execution: {},
                      availability: {},
                      resources: [],
                      planner: {},
                      contract_version: 3,
                      checksum: "b".repeat(64),
                      compiler_checksum: "c".repeat(64),
                    },
                  },
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

  it("blocks middleware dragging when the authoritative registry fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input) === "/api/runtime/middleware-nodes") {
          return new Response(
            JSON.stringify([
              {
                id: "human_in_the_loop",
                kind: "runtime.middleware.human_in_the_loop",
                title: "人工审批",
                description: "测试中间件",
                category: "control",
                icon: "Shield",
                enabled: true,
                fields: [],
              },
            ]),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("unavailable", { status: 503 });
      }),
    );

    render(createElement(NodePalette));
    await screen.findByText(/本地目录仅供查看/);
    fireEvent.click(screen.getByRole("button", { name: "中间件" }));

    expect(await screen.findByText(/节点注册表门禁不可用/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /人工审批/ }),
    ).not.toBeInTheDocument();
  });
});
