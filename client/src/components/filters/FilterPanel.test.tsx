import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultFilterState } from "../../data/filterState";
import FilterPanel from "./FilterPanel";

function renderPanel(onChange = vi.fn()) {
  render(
    <FilterPanel
      filters={{
        ...defaultFilterState,
        inputModalities: [],
        jobCapabilities: [],
        modelAuthors: [],
        series: [],
        supportedParameters: [],
      }}
      modelAuthorOptions={[]}
      onChange={onChange}
      onClear={vi.fn()}
      seriesOptions={[]}
    />,
  );
  return onChange;
}

describe("FilterPanel", () => {
  it("keeps only the two core filter rows visible by default", () => {
    const onChange = renderPanel();

    expect(screen.getByText("可接收输入")).toBeInTheDocument();
    expect(screen.getByText("可完成任务")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "文本" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "图片" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "视频生成" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "实时语音" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "语音合成" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "音乐生成" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "文档理解" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "文字对话" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "更多筛选" })).not.toBeInTheDocument();
    expect(screen.queryByText("搜索用人单位")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "文本" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ inputModalities: ["text"] }),
    );
  });

  it("uses the same disclosure button to open and close all capabilities", async () => {
    renderPanel();
    const expand = screen.getByRole("button", { name: "查看全部" });

    fireEvent.click(expand);
    expect(screen.getByRole("dialog", { name: "更多筛选" })).toBeInTheDocument();
    const collapse = screen.getByRole("button", { name: "收起全部" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(collapse);
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "更多筛选" })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "查看全部" })).toHaveFocus();
  });

  it("opens an anchored advanced panel with prioritized providers", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /更多筛选/ }));

    const dialog = screen.getByRole("dialog", { name: "更多筛选" });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "用人单位",
      "价格与上下文",
      "模型系列",
      "高级条件",
    ]);
    expect(
      within(dialog)
        .getAllByRole("radio")
        .slice(0, 4)
        .map((radio) => radio.closest("label")?.textContent),
    ).toEqual(["全部", "OpenAI", "Anthropic", "深度求索"]);

    fireEvent.click(within(dialog).getByRole("tab", { name: "价格与上下文" }));
    expect(within(dialog).getByText("工作年限/经验值")).toBeInTheDocument();
    expect(within(dialog).getByText("期望薪资")).toBeInTheDocument();
  });

  it("closes on Escape or outside press and restores focus", async () => {
    renderPanel();
    const trigger = screen.getByRole("button", { name: /更多筛选/ });

    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole("dialog", { name: "更多筛选" })).not.toBeInTheDocument();

    fireEvent.click(trigger);
    fireEvent.pointerDown(document.body);
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole("dialog", { name: "更多筛选" })).not.toBeInTheDocument();
  });
});
