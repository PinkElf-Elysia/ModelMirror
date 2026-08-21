import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SkillCreatorMiddlewareModePanel from "./SkillCreatorMiddlewareModePanel";

describe("SkillCreatorMiddlewareModePanel", () => {
  it("warns about Legacy behavior and requires an explicit upgrade click", async () => {
    const onUpgrade = vi.fn();
    render(<SkillCreatorMiddlewareModePanel legacy onUpgrade={onUpgrade} />);

    expect(screen.getByText("Legacy")).toBeVisible();
    expect(screen.getByText("此节点仍使用旧的一次性提案路径")).toBeVisible();
    expect(onUpgrade).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "确认升级为 Creator V2" }),
    );
    expect(onUpgrade).toHaveBeenCalledTimes(1);
  });

  it("explains the V2 boundary without exposing a migration action", () => {
    render(<SkillCreatorMiddlewareModePanel legacy={false} onUpgrade={vi.fn()} />);

    expect(screen.getByText("Creator V2")).toBeVisible();
    expect(screen.getByText("分析需求并创建 Creator 会话")).toBeVisible();
    expect(screen.getByText(/不会直接生成或安装 Skill/)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "确认升级为 Creator V2" }),
    ).not.toBeInTheDocument();
  });
});
