import { fireEvent, render, screen, within } from "@testing-library/react";
import { FileText, Wrench } from "lucide-react";
import { createRef } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import {
  ChatActionMenu,
  ChatActiveContextBar,
  ChatCompactHeader,
  ChatOverlayDrawer,
  type ChatActionDescriptor,
} from "./ChatConversationChrome";

describe("ChatConversationChrome", () => {
  it.each([
    ["direct", "直接对话"],
    ["auto", "智能调度"],
    ["expert", "专家模式"],
  ] as const)("renders the %s compact header", (mode, label) => {
    render(
      <MemoryRouter>
        <ChatCompactHeader
          backTo="/models"
          mode={mode}
          modelLabel="GPT-5.6 Luna"
          onOpenSettings={vi.fn()}
          settingsTriggerRef={createRef<HTMLButtonElement>()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("GPT-5.6 Luna")).toBeInTheDocument();
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提示库" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "题库" })).not.toBeInTheDocument();
    expect(screen.getByRole("banner").firstElementChild).toHaveClass("h-16");
  });

  it("groups actions, keeps blocked reasons visible, and restores trigger focus on Escape", () => {
    const triggerRef = createRef<HTMLButtonElement>();
    const onOpenChange = vi.fn();
    const actions: ChatActionDescriptor[] = [
      {
        id: "file",
        group: "content",
        label: "文件",
        description: "上传文件",
        icon: FileText,
        onSelect: vi.fn(),
      },
      {
        id: "mcp",
        group: "tools",
        label: "MCP 工具",
        description: "调用工具",
        icon: Wrench,
        status: "blocked",
        blockedReason: "智能调度暂不支持 MCP。",
      },
    ];

    render(
      <ChatActionMenu
        actions={actions}
        onOpenChange={onOpenChange}
        open
        triggerRef={triggerRef}
      />,
    );

    expect(screen.getByText("添加内容")).toBeInTheDocument();
    expect(screen.getByText("工具")).toBeInTheDocument();
    expect(screen.getByText("智能调度暂不支持 MCP。")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("removes an active context without duplicating attachment UI", () => {
    const onRemove = vi.fn();
    render(
      <ChatActiveContextBar
        contexts={[{ id: "kb", label: "资料库 · 产品文档", onRemove }]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "移除 资料库 · 产品文档" }));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it("closes the single overlay with Escape and returns focus", () => {
    const triggerRef = createRef<HTMLButtonElement>();
    const onClose = vi.fn();
    render(
      <>
        <button ref={triggerRef} type="button">打开设置</button>
        <ChatOverlayDrawer
          onClose={onClose}
          open
          title="对话设置"
          triggerRef={triggerRef}
        >
          <button type="button">抽屉操作</button>
        </ChatOverlayDrawer>
      </>,
    );

    const dialog = screen.getByRole("dialog", { name: "对话设置" });
    expect(within(dialog).getByRole("button", { name: "抽屉操作" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
