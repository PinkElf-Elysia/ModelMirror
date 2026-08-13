import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultFilterState } from "../../data/filterState";
import {
  modelAuthorOptions,
  providerOptions,
  seriesOptions,
} from "../../data/filterOptions";
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
      seriesOptions={seriesOptions}
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
    expect(screen.queryByText("搜索服务提供商")).not.toBeInTheDocument();

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

  it("opens an anchored advanced panel with the current market facets", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /更多筛选/ }));

    const dialog = screen.getByRole("dialog", { name: "更多筛选" });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "提供商与模型商",
      "价格与上下文",
      "模型系列",
      "应用分类",
      "参数与状态",
      "基准指标",
      "可完成任务",
    ]);
    expect(within(dialog).getByPlaceholderText("搜索服务提供商")).toBeInTheDocument();
    expect(within(dialog).getByText("服务提供商")).toBeInTheDocument();
    expect(within(dialog).getByText("模型商")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: "价格与上下文" }));
    expect(within(dialog).getByText("上下文长度")).toBeInTheDocument();
    expect(within(dialog).getByText("输入价格")).toBeInTheDocument();
    expect(within(dialog).getByText("输出价格")).toBeInTheDocument();
    expect(within(dialog).getByText("模型年龄")).toBeInTheDocument();
  });

  it("presents the category facet as product-facing Chinese copy", () => {
    const onChange = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /更多筛选/ }));

    const dialog = screen.getByRole("dialog", { name: "更多筛选" });
    fireEvent.click(within(dialog).getByRole("tab", { name: "应用分类" }));
    expect(within(dialog).getAllByText("应用分类")).toHaveLength(2);
    expect(
      within(dialog).getByText("按模型适用的领域筛选。"),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/OpenRouter|\?category=/)).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "编程" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ openRouterCategories: ["programming"] }),
    );
  });

  it("shows exact series, parameter, region and benchmark option groups", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /更多筛选/ }));
    const dialog = screen.getByRole("dialog", { name: "更多筛选" });

    fireEvent.click(within(dialog).getByRole("tab", { name: "模型系列" }));
    expect(within(dialog).getByRole("button", { name: "GPT" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "PaLM" })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: "参数与状态" }));
    expect(within(dialog).getByText("支持参数")).toBeInTheDocument();
    expect(within(dialog).getByText("预测内容（prediction）")).toBeInTheDocument();
    expect(within(dialog).queryByText("tool_choice")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "欧盟" })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: "基准指标" }));
    expect(within(dialog).getByText("综合能力评测")).toBeInTheDocument();
    expect(within(dialog).getByText("设计能力评测")).toBeInTheDocument();
  });

  it("prioritizes common brands and localizes Chinese model companies", () => {
    expect(providerOptions.slice(0, 7).map((option) => option.value)).toEqual([
      "OpenAI",
      "Anthropic",
      "DeepSeek",
      "Google",
      "Google AI Studio",
      "xAI",
      "Alibaba",
    ]);
    expect(providerOptions.find((option) => option.value === "DeepSeek")?.label).toBe(
      "深度求索",
    );
    expect(modelAuthorOptions.slice(0, 8).map((option) => option.value)).toEqual([
      "openai",
      "anthropic",
      "deepseek",
      "deepseek-ai",
      "google",
      "x-ai",
      "moonshotai",
      "qwen",
    ]);
    expect(modelAuthorOptions.find((option) => option.value === "moonshotai")?.label).toBe(
      "Kimi（月之暗面）",
    );
    expect(seriesOptions.slice(0, 7).map((option) => option.value)).toEqual([
      "GPT",
      "Claude",
      "DeepSeek",
      "Gemini",
      "Grok",
      "Qwen",
      "Qwen3",
    ]);
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
