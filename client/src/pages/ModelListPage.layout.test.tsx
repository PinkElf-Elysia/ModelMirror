import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultFilterState } from "../data/filterState";
import {
  ModelMarketHero,
  shouldShowFeaturedRecommendations,
} from "./ModelListPage";

describe("ModelMarketHero", () => {
  it("keeps the brand, concise status, and search as the first-screen hierarchy", () => {
    const onSearchChange = vi.fn();

    render(
      <ModelMarketHero
        adaptedCount={17}
        onsiteCount={24}
        onSearchChange={onSearchChange}
        searchTerm="vision"
      />,
    );

    expect(
      screen.getByRole("heading", { name: "AI 牛马招聘会" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("按输入能力与任务筛选模型，确认状态后直接调用。"),
    ).toBeInTheDocument();
    const status = screen.getByLabelText("模型市场状态");
    expect(status).toHaveTextContent("24 个模型");
    expect(status).toHaveTextContent("17 已适配");
    expect(status).not.toHaveTextContent("可直接调用");
    expect(status).not.toHaveTextContent("可调用数待确认");
    expect(status).not.toHaveTextContent("岗位要求");

    const search = screen.getByRole("searchbox");
    expect(search).toHaveClass("h-12");
    fireEvent.change(search, { target: { value: "audio" } });
    expect(onSearchChange).toHaveBeenCalledWith("audio");
    expect(
      screen.queryByRole("button", { name: "清空岗位要求" }),
    ).not.toBeInTheDocument();
  });

});

describe("model market recommendation layout", () => {
  it("reserves router and flagship cards for the unfiltered landing view", () => {
    expect(shouldShowFeaturedRecommendations(defaultFilterState, "")).toBe(true);
    expect(
      shouldShowFeaturedRecommendations(
        { ...defaultFilterState, jobCapabilities: ["video_generation"] },
        "",
      ),
    ).toBe(false);
    expect(
      shouldShowFeaturedRecommendations(
        { ...defaultFilterState, providers: ["OpenAI"] },
        "",
      ),
    ).toBe(false);
    expect(
      shouldShowFeaturedRecommendations(defaultFilterState, "Lyria 3"),
    ).toBe(false);
  });
});
