import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultFilterState } from "../../data/filterState";
import { recruitmentFilterTitles } from "../../theme/recruitmentTheme";
import FilterPanel from "./FilterPanel";

describe("FilterPanel", () => {
  it("shows the compact primary filters and hides advanced sections until requested", () => {
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
        matchingCount={12}
        modelAuthorOptions={[]}
        onChange={vi.fn()}
        seriesOptions={[]}
        totalCount={12}
      />,
    );

    const sectionButtons = screen
      .getAllByRole("button")
      .filter((button) =>
        Object.values(recruitmentFilterTitles).some((title) =>
          button.textContent?.includes(title),
        ),
      );
    expect(sectionButtons.slice(0, 6).map((button) => button.textContent)).toEqual([
      expect.stringContaining(recruitmentFilterTitles.inputModalities),
      expect.stringContaining(recruitmentFilterTitles.jobCapabilities),
      expect.stringContaining(recruitmentFilterTitles.provider),
      expect.stringContaining(recruitmentFilterTitles.context),
      expect.stringContaining(recruitmentFilterTitles.pricing),
      expect.stringContaining(recruitmentFilterTitles.series),
    ]);

    const sectionButton = (title: string) =>
      screen.getByText(title).closest("button");
    expect(sectionButton(recruitmentFilterTitles.jobCapabilities)).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(sectionButton(recruitmentFilterTitles.inputModalities)).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(sectionButton(recruitmentFilterTitles.provider)).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(
      screen.getByText(recruitmentFilterTitles.inputModalities).closest("section"),
    ).toHaveClass("h-full");
    expect(
      screen.getByText(recruitmentFilterTitles.jobCapabilities).closest("section"),
    ).toHaveClass("h-full");
    expect(
      screen.getByText(recruitmentFilterTitles.provider).closest("section"),
    ).toHaveClass("h-full");
    expect(sectionButton(recruitmentFilterTitles.context)).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(sectionButton(recruitmentFilterTitles.pricing)).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByText(recruitmentFilterTitles.parameters)).not.toBeInTheDocument();

    const providerSection = screen
      .getByText(recruitmentFilterTitles.provider)
      .closest("section");
    expect(providerSection).not.toBeNull();
    expect(
      within(providerSection as HTMLElement)
        .getAllByRole("radio")
        .slice(0, 4)
        .map((radio) => radio.closest("label")?.textContent),
    ).toEqual(["全部", "OpenAI", "Anthropic", "深度求索"]);

    fireEvent.click(screen.getByRole("button", { name: "更多筛选" }));
    expect(screen.getByText(recruitmentFilterTitles.parameters)).toBeInTheDocument();
    expect(sectionButton(recruitmentFilterTitles.parameters)).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });
});
